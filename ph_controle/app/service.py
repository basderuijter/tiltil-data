"""Toepassingslogica: het proces PH → controle → akkoord → pakbon."""

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
from .models import CONDITIES, Conditie, Controle, Ph
from .srs.base import SrsClient
from .store import Opslag


class ControleFout(RuntimeError):
    """De gevraagde stap mag nu niet."""


@dataclass
class PhOverzichtRegel:
    ph: Ph
    status: PhStatus
    controle: Controle | None

    @property
    def in_controle(self) -> bool:
        return self.controle is not None and not self.controle.is_akkoord

    @property
    def is_afgerond(self) -> bool:
        return self.controle is not None and self.controle.is_akkoord


@dataclass
class AkkoordResultaat:
    controle: Controle
    srs_referentie: str


class PhService:
    def __init__(self, srs: SrsClient, opslag: Opslag):
        self.srs = srs
        self.opslag = opslag

    # -- overzicht -------------------------------------------------------

    def overzicht(self) -> list[PhOverzichtRegel]:
        regels = []
        for ph in self.srs.lijst_phs():
            regels.append(
                PhOverzichtRegel(
                    ph=ph,
                    status=ph_status(ph),
                    controle=self.opslag.laatste_controle(ph.code),
                )
            )
        # Compleet en nog niet gecontroleerd bovenaan: daar is werk te doen.
        return sorted(
            regels,
            key=lambda r: (r.is_afgerond, not r.status.mag_uit_ph, r.ph.code),
        )

    # -- controle --------------------------------------------------------

    def haal_ph(self, code: str) -> Ph:
        return self.srs.haal_ph(code)

    def start_controle(self, code: str, medewerker: str = "") -> Controle:
        """Open de lopende controle, of begin een nieuwe op basis van SRS."""
        bestaand = self.opslag.open_controle(code)
        if bestaand is not None:
            if medewerker and bestaand.medewerker != medewerker:
                bestaand.medewerker = medewerker
                self.opslag.bewaar(bestaand)
            return bestaand

        ph = self.srs.haal_ph(code)
        controle = Controle(
            ph_code=ph.code,
            order_referentie=ph.order_referentie,
            regels=controle_regels_uit_ph(ph),
            medewerker=medewerker,
            gestart_op=datetime.now(),
        )
        self.opslag.bewaar_nieuw(controle)
        self.opslag.log(
            ph.code,
            "controle_gestart",
            controle_id=controle.id,
            medewerker=medewerker,
            details={"order": ph.order_referentie, "regels": len(controle.regels)},
        )
        return controle

    def _actieve_controle(self, code: str) -> Controle:
        controle = self.opslag.open_controle(code)
        if controle is None:
            raise ControleFout(f"Er loopt geen controle voor PH {code}.")
        return controle

    def scan_barcode(self, code: str, barcode: str, medewerker: str = "") -> ScanResultaat:
        controle = self._actieve_controle(code)
        resultaat = scan(controle, barcode)
        if resultaat.status == "geteld":
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

    def meld_afwijking(self, code: str, notitie: str, medewerker: str = "") -> Controle:
        """Leg vast dat deze PH niet akkoord is; blijft openstaan voor opvolging."""
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
        return controle

    # -- akkoord ---------------------------------------------------------

    def geef_akkoord(self, code: str, medewerker: str) -> AkkoordResultaat:
        """Akkoord geven, SRS afmelden en daarmee de PH vrijgeven."""
        if not medewerker.strip():
            raise ControleFout("Vul je naam in voordat je akkoord geeft.")

        controle = self._actieve_controle(code)
        check = mag_akkoord(controle)
        if not check.mag_akkoord:
            raise ControleFout(
                "Akkoord kan nog niet: " + " ".join(check.redenen)
            )

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
            raise

        self.opslag.bewaar(controle)
        self.opslag.log(
            code,
            "akkoord",
            controle_id=controle.id,
            medewerker=medewerker,
            details={"srs_referentie": referentie, "stuks": controle.totaal_geteld},
        )
        return AkkoordResultaat(controle=controle, srs_referentie=referentie)

    # -- pakbon ----------------------------------------------------------

    def pakbon_gegevens(self, code: str) -> tuple[Ph, Controle]:
        controle = self.opslag.laatste_controle(code)
        if controle is None or not controle.is_akkoord:
            raise ControleFout(
                f"Voor PH {code} is nog geen akkoord gegeven; er is dus geen pakbon."
            )
        return self.srs.haal_ph(code), controle
