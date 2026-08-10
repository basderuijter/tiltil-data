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
    ):
        self.srs = srs
        self.opslag = opslag
        self.instellingen = instellingen or laad_instellingen()
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

        return AkkoordResultaat(
            controle=controle,
            srs_referentie=referentie,
            doorlooptijd_seconden=doorlooptijd,
            volgende_ph=self.volgende_klaar(behalve=code),
        )

    # -- pakbon ----------------------------------------------------------

    def pakbon_gegevens(self, code: str) -> tuple[Ph, Controle]:
        controle = self.opslag.laatste_controle(code)
        if controle is None or not controle.is_akkoord:
            raise ControleFout(
                f"Voor PH {code} is nog geen akkoord gegeven; er is dus geen pakbon."
            )
        return self.srs.haal_ph(code), controle
