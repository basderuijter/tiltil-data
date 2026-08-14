"""Verzendlaag voor de PH-app: van kratten naar labels op de printer.

De Sendcloud-client en de printer komen uit de losse scan-app en zijn async.
De PH-app is synchroon, dus dit is de brug: één plek waar een event loop wordt
opgestart en waar demomodus, giftcards en de kratverdeling geregeld worden.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from . import demo as nepdata
from .config import VerzendInstellingen
from .kratten import Krat, Verdeling, is_giftcard, verdeel, zonder_giftcards
from .models import Label, Order, ShippingOption
from .printing import PrintError, Printer
from .sendcloud import OrderNotFound, SendcloudClient, SendcloudError
from .stations import Station, StationError, load_stations, resolve_station

__all__ = [
    "Adres",
    "Krat",
    "OrderNotFound",
    "PrintError",
    "SendcloudError",
    "StationError",
    "Verzendservice",
    "ZendingResultaat",
]


@dataclass
class Adres:
    """Handmatig adres voor een zending zonder order in Sendcloud."""

    naam: str
    adres: str
    postcode: str
    plaats: str
    landcode: str = "NL"
    bedrijf: str = ""
    gewicht_kg: float = 1.0


@dataclass
class ZendingResultaat:
    labels: list[Label] = field(default_factory=list)
    methode_code: str = ""
    methode_naam: str = ""
    vervoerder: str = ""
    geprint: bool = False
    printfout: str = ""
    # False als de kratverdeling niet op de Sendcloud-regels te leggen was en
    # er teruggevallen is op een willekeurige verdeling over de dozen.
    exacte_kratverdeling: bool = True
    overgeslagen_giftcards: int = 0

    @property
    def tracking(self) -> list[str]:
        return [label.tracking_number for label in self.labels if label.tracking_number]


class Verzendservice:
    """Alles wat de PH-app met Sendcloud en de labelprinter doet."""

    def __init__(self, instellingen: VerzendInstellingen):
        self.instellingen = instellingen

    # -- plumbing --------------------------------------------------------

    @property
    def actief(self) -> bool:
        return self.instellingen.actief

    def _draai(self, opdracht):
        """Voer een async opdracht uit vanuit synchrone code."""

        async def _uitvoeren():
            async with httpx.AsyncClient() as http:
                return await opdracht(
                    SendcloudClient(self.instellingen, http),
                    Printer(self.instellingen, http),
                )

        return asyncio.run(_uitvoeren())

    def _station(self, station_id: str = "") -> Station | None:
        return resolve_station(self.instellingen, station_id or None)

    def stations(self) -> list[Station]:
        return load_stations(self.instellingen)

    # -- opzoeken --------------------------------------------------------

    def zoek_order(self, ordernummer: str) -> Order:
        if self.instellingen.demo_mode:
            return nepdata.demo_order(ordernummer)
        return self._draai(lambda client, _printer: client.find_order(ordernummer))

    def verzendmethodes(self, order: Order) -> list[ShippingOption]:
        if self.instellingen.demo_mode:
            return nepdata.demo_shipping_options(order)
        return self._draai(lambda client, _printer: client.shipping_options(order))

    # -- labels ----------------------------------------------------------

    def maak_labels(
        self,
        ordernummer: str,
        kratten: list[Krat],
        *,
        methode_code: str = "",
        station_id: str = "",
    ) -> ZendingResultaat:
        """Meld de order aan bij Sendcloud, één label per krat, en print ze.

        Giftcards gaan er eerst uit: die zijn in Shopify al digitaal afgemeld
        en zouden anders een doos krijgen die nooit verstuurd wordt.
        """
        if not kratten:
            raise SendcloudError("Geen kratten om te verzenden.")

        patroon = self.instellingen.giftcard_patroon
        digitaal = sum(
            regel.aantal
            for krat in kratten
            for regel in krat.regels
            if is_giftcard(regel, patroon)
        )
        fysiek = zonder_giftcards(kratten, patroon)
        if not fysiek:
            # Alleen giftcards: niets om te versturen, dus ook geen label.
            return ZendingResultaat(overgeslagen_giftcards=digitaal)

        order = self.zoek_order(ordernummer)
        code = methode_code or order.current_shipping_option_code
        if not code:
            code = self.instellingen.standaard_verzendmethode
        if not code:
            raise SendcloudError(
                f"Order {ordernummer} heeft geen verzendmethode in Sendcloud en "
                "STANDAARD_VERZENDMETHODE is niet gezet."
            )

        verdeling = (
            verdeel(fysiek, order.items)
            if len(fysiek) > 1
            else Verdeling(pakketten=[], exact=True)
        )

        if self.instellingen.demo_mode:
            labels = nepdata.demo_labels(order, len(fysiek))
        else:
            labels = self._draai(
                lambda client, _printer: client.create_labels(
                    order,
                    code,
                    len(fysiek),
                    parcels=verdeling.pakketten or None,
                )
            )

        resultaat = ZendingResultaat(
            labels=labels,
            methode_code=code,
            methode_naam=order.current_shipping_option_name or code,
            vervoerder=_vervoerder(code, order),
            exacte_kratverdeling=verdeling.exact,
            overgeslagen_giftcards=digitaal,
        )
        self._print(resultaat, station_id)
        return resultaat

    def losse_zending(
        self,
        adres: Adres,
        *,
        methode_code: str,
        aantal: int = 1,
        referentie: str = "",
        station_id: str = "",
    ) -> ZendingResultaat:
        """Een zending zonder PH: retour, nazending of klantenservicepakket."""
        if aantal < 1:
            raise SendcloudError("Aantal pakketten moet minimaal 1 zijn.")
        if not methode_code:
            raise SendcloudError("Kies een verzendmethode.")

        order = Order(
            id="",
            order_number=referentie or "los",
            recipient_name=adres.naam,
            company_name=adres.bedrijf,
            address=adres.adres,
            postal_code=adres.postcode,
            city=adres.plaats,
            country_code=adres.landcode,
            weight_kg=adres.gewicht_kg,
        )

        if self.instellingen.demo_mode:
            labels = nepdata.demo_labels(order, aantal)
        else:
            labels = []
            for _ in range(aantal):
                # Elk pakket is een eigen zending: zonder order in Sendcloud is
                # er niets om een multicollo aan op te hangen.
                labels += self._draai(
                    lambda client, _printer: client.create_extra_label(order, methode_code)
                )

        resultaat = ZendingResultaat(
            labels=labels,
            methode_code=methode_code,
            methode_naam=methode_code,
            vervoerder=methode_code.split(":")[0],
        )
        self._print(resultaat, station_id)
        return resultaat

    # -- printen ---------------------------------------------------------

    def print_labels(self, labels: list[Label], station_id: str = "") -> None:
        if not labels:
            raise PrintError("Er zijn geen labels om te printen.")
        station = self._station(station_id)
        self._draai(lambda _client, printer: printer.print_labels(labels, station))

    def _print(self, resultaat: ZendingResultaat, station_id: str) -> None:
        """Printen mag mislukken zonder het label kwijt te raken.

        Het label is dan al gemaakt en opgeslagen; opnieuw printen kan met een
        knop, wat vaker nodig is dan wat ook — thermische printers verfrommelen.
        """
        try:
            self.print_labels(resultaat.labels, station_id)
            resultaat.geprint = True
        except (PrintError, StationError) as fout:
            resultaat.printfout = str(fout)


def _vervoerder(code: str, order: Order) -> str:
    """De vervoerder achter een verzendoptie.

    Shopify maakt alleen een klikbare track & trace als de fulfilment de
    vervoerder benoemt, dus dit hoort mee te reizen met het trackingnummer.
    """
    if ":" in code:
        return code.split(":", 1)[0]
    return (order.current_shipping_option_name or "").split(" ")[0].lower()
