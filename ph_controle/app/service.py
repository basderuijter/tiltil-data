"""Toepassingslogica: het proces PH → controle → akkoord → pakbon,
plus signalering van vastlopers en de plattegrond van de hal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .completeness import (
    AkkoordCheck,
    PhStatus,
    ScanResultaat,
    controle_regels_uit_ph,
    mag_akkoord,
    ph_status,
    scan,
)
from .config import Instellingen, laad_instellingen
from .hal import Wand, bouw_indeling
from .models import CONDITIES, Conditie, Controle, Observatie, Onderzoek, Ph
from .signalen import Drempels, Signaal, beoordeel, haltoestand, vingerafdruk
from .srs.base import SrsClient
from .store import Opslag
from .verzending import (
    Krat,
    KratRegel,
    PrintError,
    SendcloudError,
    StationError,
    Verzendservice,
)


class ControleFout(RuntimeError):
    """De gevraagde stap mag nu niet."""


@dataclass
class PhOverzichtRegel:
    ph: Ph
    status: PhStatus
    controle: Controle | None
    signaal: Signaal
    observatie: Observatie | None = None
    onderzoek: Onderzoek | None = None

    @property
    def in_controle(self) -> bool:
        return self.controle is not None and not self.controle.is_akkoord

    @property
    def is_afgerond(self) -> bool:
        return self.controle is not None and self.controle.is_akkoord

    @property
    def toestand(self) -> str:
        return haltoestand(self.status, self.signaal, self.controle)


@dataclass
class AkkoordResultaat:
    controle: Controle
    srs_referentie: str
    doorlooptijd_seconden: float = 0.0
    volgende_ph: str = ""
    labels: int = 0
    label_fout: str = ""


@dataclass
class Statistieken:
    klaar: int = 0
    onderweg: int = 0
    vastlopers: int = 0
    let_op: int = 0
    afgemeld: int = 0
    gemiddelde_controletijd: float | None = None


class PhService:
    def __init__(
        self,
        srs: SrsClient,
        opslag: Opslag,
        instellingen: Instellingen | None = None,
        verzending: Verzendservice | None = None,
    ):
        self.srs = srs
        self.opslag = opslag
        self.instellingen = instellingen or laad_instellingen()
        self.verzending = verzending or Verzendservice(self.instellingen.verzending)
        self.drempels = Drempels(
            letop_uren=self.instellingen.letop_uren,
            vastloper_uren=self.instellingen.vastloper_uren,
            stilstand_uren=self.instellingen.stilstand_uren,
        )

    # -- overzicht en signalering ----------------------------------------

    def _beoordeel(
        self,
        ph: Ph,
        status: PhStatus,
        controle: Controle | None,
        onderzoek: Onderzoek | None,
        nu: datetime,
    ) -> tuple[Signaal, Observatie]:
        observatie = self.opslag.noteer_waarneming(
            ph.code,
            order_referentie=ph.order_referentie,
            vingerafdruk=vingerafdruk(ph),
            is_compleet=status.mag_uit_ph,
            moment=nu,
        )
        signaal = beoordeel(
            ph,
            status,
            observatie,
            nu,
            self.drempels,
            controle=controle,
            onderzoek=onderzoek,
        )
        return signaal, observatie

    def overzicht(self, nu: datetime | None = None) -> list[PhOverzichtRegel]:
        nu = nu or datetime.now()
        controles = self.opslag.laatste_controles()
        onderzoeken = {o.ph_code: o for o in self.opslag.open_onderzoeken()}

        regels: list[PhOverzichtRegel] = []
        for ph in self.srs.lijst_phs():
            status = ph_status(ph)
            controle = controles.get(ph.code)
            onderzoek = onderzoeken.get(ph.code)
            signaal, observatie = self._beoordeel(ph, status, controle, onderzoek, nu)
            regels.append(
                PhOverzichtRegel(
                    ph=ph,
                    status=status,
                    controle=controle,
                    signaal=signaal,
                    observatie=observatie,
                    onderzoek=onderzoek,
                )
            )

        # Vastlopers eerst (die kosten tijd), dan wat klaarstaat, dan de rest.
        return sorted(
            regels,
            key=lambda r: (
                not r.signaal.is_vastloper,
                r.is_afgerond,
                not r.status.mag_uit_ph,
                -r.signaal.leeftijd_uren,
                r.ph.code,
            ),
        )

    def statistieken(self, regels: list[PhOverzichtRegel]) -> Statistieken:
        tijden = self.opslag.controletijden(
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        )
        return Statistieken(
            klaar=sum(1 for r in regels if r.status.mag_uit_ph and not r.is_afgerond),
            onderweg=sum(1 for r in regels if not r.status.mag_uit_ph and not r.is_afgerond),
            vastlopers=sum(1 for r in regels if r.signaal.is_vastloper),
            let_op=sum(1 for r in regels if r.signaal.niveau == "let_op"),
            afgemeld=sum(1 for r in regels if r.is_afgerond),
            gemiddelde_controletijd=(sum(tijden) / len(tijden)) if tijden else None,
        )

    def hal(self, nu: datetime | None = None) -> tuple[list[Wand], list[PhOverzichtRegel]]:
        """Plattegrond van de hal met per vak de actuele toestand."""
        regels = self.overzicht(nu)
        per_code = {r.ph.code: r for r in regels}
        wanden = bouw_indeling(per_code.keys(), self.instellingen.hal_bestand)

        for wand in wanden:
            for vak in wand.vakken:
                regel = per_code.get(vak.code)
                if regel is None:
                    vak.toestand = "beschikbaar"
                    continue
                vak.ph = regel.ph
                vak.status = regel.status
                vak.signaal = regel.signaal
                vak.controle = regel.controle
                vak.toestand = regel.toestand
        return wanden, regels

    # -- snel openen ------------------------------------------------------

    def zoek(self, term: str) -> str | None:
        """Vind een PH via de scan van een PH-label, orderreferentie of artikel.

        Scheelt zoeken in de lijst: de scanner opent het juiste scherm.
        """
        term = (term or "").strip()
        if not term:
            return None
        genormaliseerd = term.upper().replace(" ", "")

        phs = self.srs.lijst_phs()
        for ph in phs:
            if ph.code.upper().replace(" ", "") == genormaliseerd:
                return ph.code
        for ph in phs:
            if ph.order_referentie.upper().replace(" ", "") == genormaliseerd:
                return ph.code

        # Artikelbarcode: alleen bruikbaar als hij bij precies één PH hoort.
        treffers = [ph.code for ph in phs if ph.regel(term) is not None]
        if len(treffers) == 1:
            return treffers[0]

        # Losse cijfers van een PH-label, bijvoorbeeld "12" voor G-PH.12.
        if genormaliseerd.isdigit():
            staart = [ph.code for ph in phs if ph.code.rstrip().endswith(genormaliseerd.zfill(2))]
            if len(staart) == 1:
                return staart[0]
        return None

    def volgende_klaar(self, behalve: str = "") -> str:
        """De eerstvolgende PH die gecontroleerd kan worden."""
        for regel in self.overzicht():
            if regel.ph.code == behalve or regel.is_afgerond or regel.in_controle:
                continue
            if regel.status.mag_uit_ph:
                return regel.ph.code
        return ""

    # -- controle --------------------------------------------------------

    def haal_ph(self, code: str) -> Ph:
        return self.srs.haal_ph(code)

    def ph_met_signaal(
        self, code: str, nu: datetime | None = None
    ) -> tuple[Ph, PhStatus, Signaal, Onderzoek | None]:
        nu = nu or datetime.now()
        ph = self.srs.haal_ph(code)
        status = ph_status(ph)
        controle = self.opslag.laatste_controle(code)
        onderzoek = self.opslag.open_onderzoek(code)
        signaal, _ = self._beoordeel(ph, status, controle, onderzoek, nu)
        return ph, status, signaal, onderzoek

    def start_controle(
        self, code: str, medewerker: str = "", *, deellevering: bool = False
    ) -> Controle:
        """Open de lopende controle, of begin een nieuwe op basis van SRS."""
        bestaand = self.opslag.open_controle(code)
        if bestaand is not None:
            if medewerker and bestaand.medewerker != medewerker:
                bestaand.medewerker = medewerker
                self.opslag.bewaar(bestaand)
            return bestaand

        ph = self.srs.haal_ph(code)
        eerdere = self.opslag.leveringen(code)
        # Elke deellevering krijgt een eigen referentie: WEB-1042-1, WEB-1042-2.
        # Ook de eerste, anders is niet te zien dat er meer volgt.
        referentie = (
            f"{ph.order_referentie}-{len(eerdere) + 1}"
            if deellevering or eerdere
            else ph.order_referentie
        )
        controle = Controle(
            ph_code=ph.code,
            order_referentie=ph.order_referentie,
            regels=controle_regels_uit_ph(ph),
            medewerker=medewerker,
            gestart_op=datetime.now(),
            is_deellevering=deellevering,
            levering_referentie=referentie,
        )
        self.opslag.bewaar_nieuw(controle)
        self.opslag.log(
            ph.code,
            "controle_gestart",
            controle_id=controle.id,
            medewerker=medewerker,
            details={
                "order": ph.order_referentie,
                "regels": len(controle.regels),
                "deellevering": deellevering,
                "referentie": referentie,
            },
        )
        return controle

    def zet_krat(self, code: str, krat: int, medewerker: str = "") -> Controle:
        """Vanaf nu gaan gescande artikelen in deze krat (= doos = label)."""
        controle = self._actieve_controle(code)
        if krat < 1:
            raise ControleFout("Kratnummer begint bij 1.")
        if krat > controle.kratten + 1:
            raise ControleFout(
                f"Krat {krat} bestaat nog niet; pak eerst krat {controle.kratten + 1}."
            )
        controle.actieve_krat = krat
        controle.kratten = max(controle.kratten, krat)
        self.opslag.bewaar(controle)
        self.opslag.log(
            code,
            "krat_gewisseld",
            controle_id=controle.id,
            medewerker=medewerker or controle.medewerker,
            details={"krat": krat, "kratten": controle.kratten},
        )
        return controle

    def verwijder_lege_kratten(self, controle: Controle) -> None:
        """Een aangemaakte maar leeg gebleven krat kost anders een leeg label."""
        while controle.kratten > 1 and not controle.krat_regels(controle.kratten):
            controle.kratten -= 1
        controle.actieve_krat = min(controle.actieve_krat, controle.kratten)

    def _actieve_controle(self, code: str) -> Controle:
        controle = self.opslag.open_controle(code)
        if controle is None:
            raise ControleFout(f"Er loopt geen controle voor PH {code}.")
        return controle

    def scan_barcode(self, code: str, barcode: str, medewerker: str = "") -> ScanResultaat:
        controle = self._actieve_controle(code)
        resultaat = scan(controle, barcode)
        if resultaat.status == "geteld" and resultaat.regel is not None:
            resultaat.regel.tel_in_krat(controle.actieve_krat)
            self.opslag.bewaar(controle)
        self.opslag.log(
            code,
            f"scan_{resultaat.status}",
            controle_id=controle.id,
            medewerker=medewerker or controle.medewerker,
            details={"barcode": barcode, "melding": resultaat.melding},
        )
        return resultaat

    def zet_regel(
        self,
        code: str,
        barcode: str,
        *,
        aantal: int | None = None,
        conditie: Conditie | None = None,
        notitie: str | None = None,
        medewerker: str = "",
    ) -> Controle:
        """Handmatige correctie: aantal, staat van het product of een notitie."""
        controle = self._actieve_controle(code)
        regel = controle.regel(barcode)
        if regel is None:
            raise ControleFout(f"Barcode {barcode} hoort niet bij PH {code}.")

        if aantal is not None:
            if aantal < 0:
                raise ControleFout("Aantal kan niet negatief zijn.")
            if aantal > regel.aantal_verwacht:
                raise ControleFout(
                    f"Er zijn {regel.aantal_verwacht} stuks besteld; {aantal} kan niet."
                )
            regel.aantal_geteld = aantal
            regel.herverdeel(controle.actieve_krat)
        if conditie is not None:
            if conditie not in CONDITIES:
                raise ControleFout(f"Onbekende conditie '{conditie}'.")
            regel.conditie = conditie
        if notitie is not None:
            regel.notitie = notitie.strip()

        self.opslag.bewaar(controle)
        self.opslag.log(
            code,
            "regel_aangepast",
            controle_id=controle.id,
            medewerker=medewerker or controle.medewerker,
            details={
                "barcode": barcode,
                "aantal": regel.aantal_geteld,
                "conditie": regel.conditie,
                "notitie": regel.notitie,
            },
        )
        return controle

    def controlestatus(self, code: str) -> AkkoordCheck:
        return mag_akkoord(self._actieve_controle(code))

    # -- afwijkingen en onderzoek ----------------------------------------

    def meld_afwijking(self, code: str, notitie: str, medewerker: str = "") -> Controle:
        """Leg vast dat deze PH niet akkoord is en zet hem in onderzoek."""
        controle = self._actieve_controle(code)
        controle.afwijking_notitie = notitie.strip()
        if medewerker:
            controle.medewerker = medewerker
        self.opslag.bewaar(controle)
        self.opslag.log(
            code,
            "afwijking_gemeld",
            controle_id=controle.id,
            medewerker=medewerker or controle.medewerker,
            details={"notitie": controle.afwijking_notitie},
        )
        self.start_onderzoek(
            code,
            reden="Afwijking bij de controle",
            notitie=controle.afwijking_notitie,
            medewerker=medewerker or controle.medewerker,
        )
        return controle

    def start_onderzoek(
        self, code: str, *, reden: str, notitie: str = "", medewerker: str = ""
    ) -> Onderzoek:
        """Zet een PH in onderzoek; blijft staan tot iemand hem afrondt."""
        bestaand = self.opslag.open_onderzoek(code)
        if bestaand is not None:
            return bestaand

        onderzoek = self.opslag.start_onderzoek(
            Onderzoek(
                ph_code=code,
                reden=reden.strip() or "Onbekend",
                notitie=notitie.strip(),
                geopend_op=datetime.now(),
                geopend_door=medewerker,
            )
        )
        self.opslag.log(
            code,
            "onderzoek_gestart",
            medewerker=medewerker,
            details={"reden": onderzoek.reden, "notitie": onderzoek.notitie},
        )
        return onderzoek

    def rond_onderzoek_af(self, code: str, *, oplossing: str, medewerker: str = "") -> None:
        onderzoek = self.opslag.open_onderzoek(code)
        if onderzoek is None or onderzoek.id is None:
            raise ControleFout(f"Er loopt geen onderzoek voor PH {code}.")
        self.opslag.rond_onderzoek_af(
            onderzoek.id,
            oplossing=oplossing.strip(),
            door=medewerker,
            moment=datetime.now(),
        )
        self.opslag.log(
            code,
            "onderzoek_afgerond",
            medewerker=medewerker,
            details={"oplossing": oplossing.strip()},
        )

    def vastlopers(self, nu: datetime | None = None) -> list[PhOverzichtRegel]:
        return [r for r in self.overzicht(nu) if r.signaal.vraagt_aandacht]

    # -- akkoord ---------------------------------------------------------

    def geef_akkoord(self, code: str, medewerker: str) -> AkkoordResultaat:
        """Akkoord geven, SRS afmelden en daarmee de PH vrijgeven."""
        if not medewerker.strip():
            raise ControleFout("Vul je naam in voordat je akkoord geeft.")

        controle = self._actieve_controle(code)
        check = mag_akkoord(controle)
        if not check.mag_akkoord:
            raise ControleFout("Akkoord kan nog niet: " + " ".join(check.redenen))

        ph = self.srs.haal_ph(code)
        controle.akkoord_door = medewerker.strip()
        controle.medewerker = controle.medewerker or medewerker.strip()
        controle.akkoord_op = datetime.now()

        # Eerst SRS; mislukt dat, dan blijft de controle openstaan.
        try:
            referentie = self.srs.meld_ph_gereed(ph, controle)
        except Exception as fout:
            controle.akkoord_op = None
            controle.akkoord_door = ""
            self.opslag.log(
                code,
                "afmelding_mislukt",
                controle_id=controle.id,
                medewerker=medewerker,
                details={"fout": str(fout)},
            )
            self.start_onderzoek(
                code,
                reden="Afmelding naar SRS mislukt",
                notitie=str(fout),
                medewerker=medewerker,
            )
            raise

        self.verwijder_lege_kratten(controle)
        self.opslag.bewaar(controle)
        doorlooptijd = (
            (controle.akkoord_op - controle.gestart_op).total_seconds()
            if controle.gestart_op
            else 0.0
        )
        self.opslag.log(
            code,
            "akkoord",
            controle_id=controle.id,
            medewerker=medewerker,
            details={
                "srs_referentie": referentie,
                "stuks": controle.totaal_geteld,
                "seconden": round(doorlooptijd),
            },
        )

        if self.opslag.open_onderzoek(code) is not None:
            self.rond_onderzoek_af(
                code, oplossing="Order alsnog gecontroleerd en afgemeld", medewerker=medewerker
            )

        # Pas hierna het label. De controle is akkoord en SRS weet ervan; als
        # Sendcloud plat ligt mag dat het proces niet ophouden.
        aantal_labels = self._maak_labels(ph, controle, medewerker=medewerker)

        return AkkoordResultaat(
            controle=controle,
            srs_referentie=referentie,
            doorlooptijd_seconden=doorlooptijd,
            volgende_ph=self.volgende_klaar(behalve=code),
            labels=aantal_labels,
            label_fout=controle.label_fout,
        )

    # -- labels ----------------------------------------------------------

    def _kratten_van(self, controle: Controle) -> list[Krat]:
        return [
            Krat(
                nummer=nummer,
                regels=[
                    KratRegel(
                        omschrijving=regel.omschrijving,
                        aantal=regel.aantal_in_krat(nummer),
                        barcode=regel.barcode,
                        sku=regel.sku,
                    )
                    for regel in controle.krat_regels(nummer)
                ],
            )
            for nummer in range(1, controle.kratten + 1)
        ]

    def _maak_labels(self, ph: Ph, controle: Controle, *, medewerker: str = "") -> int:
        """Maak en print de labels; fouten blokkeren de afmelding niet."""
        if not self.verzending.actief:
            return 0
        if controle.id is None:
            return 0

        try:
            resultaat = self.verzending.maak_labels(
                ph.order_referentie,
                self._kratten_van(controle),
                station_id=self.instellingen.station_id,
            )
        except (SendcloudError, PrintError, StationError, ValueError) as fout:
            controle.label_fout = str(fout)
            self.opslag.bewaar(controle)
            self.opslag.log(
                controle.ph_code,
                "label_mislukt",
                controle_id=controle.id,
                medewerker=medewerker,
                details={"fout": str(fout)},
            )
            self.start_onderzoek(
                controle.ph_code,
                reden="Label niet gemaakt",
                notitie=str(fout),
                medewerker=medewerker,
            )
            return 0

        controle.zending_methode = resultaat.methode_naam
        controle.zending_vervoerder = resultaat.vervoerder
        controle.tracking = resultaat.tracking
        controle.labels_geprint = resultaat.geprint
        controle.label_fout = resultaat.printfout
        controle.kratverdeling_exact = resultaat.exacte_kratverdeling
        controle.giftcards_overgeslagen = resultaat.overgeslagen_giftcards
        self.opslag.bewaar_labels(controle.id, controle.ph_code, resultaat.labels)
        self.opslag.bewaar(controle)
        self.opslag.log(
            controle.ph_code,
            "label_gemaakt",
            controle_id=controle.id,
            medewerker=medewerker,
            details={
                "labels": len(resultaat.labels),
                "tracking": resultaat.tracking,
                "vervoerder": resultaat.vervoerder,
                "geprint": resultaat.geprint,
                "printfout": resultaat.printfout,
                "kratverdeling_exact": resultaat.exacte_kratverdeling,
                "giftcards_overgeslagen": resultaat.overgeslagen_giftcards,
            },
        )
        return len(resultaat.labels)

    def print_label_opnieuw(self, code: str, medewerker: str = "") -> int:
        """Opnieuw printen van een bestaand label.

        Een thermische printer verfrommelt labels; dat gebeurt vaker dan alle
        andere storingen samen, dus dit moet één knop zijn en geen nieuw label
        (dat zou een tweede zending aanmaken).
        """
        controle = self.opslag.laatste_controle(code)
        if controle is None or controle.id is None or not controle.is_akkoord:
            raise ControleFout(f"Voor PH {code} is nog geen levering afgerond.")

        labels = self.opslag.labels(controle.id)
        if not labels:
            raise ControleFout(
                "Er is nog geen label voor deze levering; maak het eerst aan."
            )
        try:
            self.verzending.print_labels(labels, self.instellingen.station_id)
        except (PrintError, StationError) as fout:
            controle.label_fout = str(fout)
            self.opslag.bewaar(controle)
            raise ControleFout(f"Printen mislukt: {fout}") from fout

        controle.labels_geprint = True
        controle.label_fout = ""
        self.opslag.bewaar(controle)
        self.opslag.log(
            code,
            "label_opnieuw_geprint",
            controle_id=controle.id,
            medewerker=medewerker,
            details={"labels": len(labels)},
        )
        return len(labels)

    def maak_label_alsnog(self, code: str, medewerker: str = "") -> int:
        """Tweede poging nadat Sendcloud of de printer eerder niet meewerkte."""
        controle = self.opslag.laatste_controle(code)
        if controle is None or controle.id is None or not controle.is_akkoord:
            raise ControleFout(f"Voor PH {code} is nog geen levering afgerond.")
        if self.opslag.labels(controle.id):
            return self.print_label_opnieuw(code, medewerker)

        ph = self.srs.haal_ph(code)
        aantal = self._maak_labels(ph, controle, medewerker=medewerker)
        if not aantal and controle.label_fout:
            raise ControleFout(controle.label_fout)
        return aantal

    def losse_zending(
        self,
        adres,
        *,
        methode_code: str,
        aantal: int = 1,
        referentie: str = "",
        medewerker: str = "",
    ):
        """Zending zonder PH: retour, nazending of klantenservicepakket."""
        resultaat = self.verzending.losse_zending(
            adres,
            methode_code=methode_code,
            aantal=aantal,
            referentie=referentie,
            station_id=self.instellingen.station_id,
        )
        self.opslag.log(
            referentie or "los",
            "losse_zending",
            medewerker=medewerker,
            details={
                "naam": adres.naam,
                "plaats": adres.plaats,
                "labels": len(resultaat.labels),
                "tracking": resultaat.tracking,
                "geprint": resultaat.geprint,
                "printfout": resultaat.printfout,
            },
        )
        return resultaat

    # -- pakbon ----------------------------------------------------------

    def pakbon_gegevens(self, code: str) -> tuple[Ph, Controle, list[dict]]:
        """Pakbon per krat: één vel per doos, met de inhoud van díe doos.

        Eén pakbon in één van twee dozen laat de ontvanger van de andere doos
        in het ongewisse, dus elke doos krijgt zijn eigen vel met 'doos 1 van 2'.
        """
        controle = self.opslag.laatste_controle(code)
        if controle is None or not controle.is_akkoord:
            raise ControleFout(
                f"Voor PH {code} is nog geen akkoord gegeven; er is dus geen pakbon."
            )

        tracking = self.opslag.label_kratten(controle.id) if controle.id else {}
        kratten = [
            {
                "nummer": nummer,
                "van": controle.kratten,
                "regels": [
                    {
                        "omschrijving": regel.omschrijving,
                        "barcode": regel.barcode,
                        "sku": regel.sku,
                        "aantal": regel.aantal_in_krat(nummer),
                    }
                    for regel in controle.krat_regels(nummer)
                ],
                "tracking": tracking.get(nummer, ""),
            }
            for nummer in range(1, controle.kratten + 1)
        ]
        return self.srs.haal_ph(code), controle, kratten

    def nog_te_leveren(self, code: str) -> list[dict]:
        """Wat er na de afgeronde leveringen nog van deze order openstaat."""
        ph = self.srs.haal_ph(code)
        geleverd: dict[str, int] = {}
        for levering in self.opslag.leveringen(code):
            for regel in levering.regels:
                geleverd[regel.barcode] = geleverd.get(regel.barcode, 0) + regel.aantal_geteld
        openstaand = []
        for regel in ph.regels:
            rest = regel.aantal_besteld - geleverd.get(regel.barcode, 0)
            if rest > 0:
                openstaand.append(
                    {"omschrijving": regel.omschrijving, "barcode": regel.barcode, "aantal": rest}
                )
        return openstaand
