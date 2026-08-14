"""Signalering: welke PH vraagt aandacht en welke loopt vast?

SRS houdt geen historie bij van hoe lang een order in een pigeonhole ligt, dus
de app kijkt zelf: sinds wanneer ligt er iets in, wanneer veranderde er voor
het laatst iets, en hoe lang staat een complete PH al te wachten op controle.
Pure logica, zonder I/O.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from .completeness import COMPLEET, TEVEEL, PhStatus
from .models import Controle, Observatie, Onderzoek, Ph

NORMAAL = "normaal"
LET_OP = "let_op"
VASTLOPER = "vastloper"

NIVEAU_LABELS = {
    NORMAAL: "Loopt",
    LET_OP: "Let op",
    VASTLOPER: "Vastloper",
}

# Toestanden voor de plattegrond van de hal.
BESCHIKBAAR = "beschikbaar"
VULLEND = "vullend"
KLAAR = "klaar"
IN_CONTROLE = "in_controle"
AFGEMELD = "afgemeld"

TOESTAND_LABELS = {
    BESCHIKBAAR: "Leeg / beschikbaar",
    VULLEND: "Wordt gevuld",
    KLAAR: "Klaar om te controleren",
    IN_CONTROLE: "In controle",
    AFGEMELD: "Gecontroleerd, mag leeg",
    LET_OP: "Let op",
    VASTLOPER: "Vastloper",
}


@dataclass(frozen=True)
class Drempels:
    letop_uren: float = 4.0
    vastloper_uren: float = 24.0
    stilstand_uren: float = 8.0


def beschrijf_duur(uren: float) -> str:
    """Korte, leesbare duur: '35 min', '6 uur', '2 dagen'."""
    if uren < 1:
        return f"{max(1, int(round(uren * 60)))} min"
    if uren < 24:
        return f"{uren:.0f} uur"
    dagen = uren / 24
    return f"{dagen:.0f} dag" if dagen < 2 else f"{dagen:.0f} dagen"


def vingerafdruk(ph: Ph) -> str:
    """Verandert zodra er iets in de PH bij komt of uit gaat."""
    inhoud = ";".join(
        f"{r.barcode}:{r.aantal_in_ph}/{r.aantal_besteld}"
        for r in sorted(ph.regels, key=lambda r: r.barcode)
    )
    return hashlib.sha1(inhoud.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Signaal:
    niveau: str
    redenen: tuple[str, ...] = ()
    leeftijd_uren: float = 0.0
    stilstand_uren: float = 0.0
    wacht_op_controle_uren: float = 0.0

    @property
    def is_vastloper(self) -> bool:
        return self.niveau == VASTLOPER

    @property
    def vraagt_aandacht(self) -> bool:
        return self.niveau in (LET_OP, VASTLOPER)

    @property
    def label(self) -> str:
        return NIVEAU_LABELS[self.niveau]

    @property
    def leeftijd(self) -> str:
        return beschrijf_duur(self.leeftijd_uren)


def _uren(vanaf: datetime | None, nu: datetime) -> float:
    if vanaf is None:
        return 0.0
    return max(0.0, (nu - vanaf).total_seconds() / 3600)


def beoordeel(
    ph: Ph,
    status: PhStatus,
    observatie: Observatie | None,
    nu: datetime,
    drempels: Drempels,
    *,
    controle: Controle | None = None,
    onderzoek: Onderzoek | None = None,
) -> Signaal:
    """Bepaal of deze PH normaal loopt, aandacht vraagt of vastloopt."""
    leeftijd = _uren(observatie.eerst_gezien if observatie else None, nu)
    stilstand = _uren(observatie.laatst_gewijzigd if observatie else None, nu)
    wachtend = _uren(observatie.compleet_sinds if observatie else None, nu)

    def signaal(niveau: str, *redenen: str) -> Signaal:
        return Signaal(niveau, tuple(redenen), leeftijd, stilstand, wachtend)

    if onderzoek is not None and onderzoek.is_open:
        return signaal(VASTLOPER, f"In onderzoek: {onderzoek.reden}")

    if controle is not None and controle.is_akkoord:
        return signaal(NORMAAL)

    if controle is not None and controle.afwijking_notitie:
        return signaal(VASTLOPER, f"Afwijking gemeld: {controle.afwijking_notitie}")

    if status.status == TEVEEL:
        niveau = VASTLOPER if leeftijd >= drempels.letop_uren else LET_OP
        return signaal(niveau, "Er ligt meer in de PH dan besteld.")

    if status.status == COMPLEET:
        # Compleet maar nog niemand die hem controleert: hier gaat snelheid weg.
        if wachtend >= drempels.vastloper_uren:
            return signaal(
                VASTLOPER, f"Staat al {beschrijf_duur(wachtend)} klaar zonder controle."
            )
        if wachtend >= drempels.letop_uren:
            return signaal(LET_OP, f"Wacht {beschrijf_duur(wachtend)} op controle.")
        return signaal(NORMAAL)

    # Nog niet compleet: te lang open, of het picken staat stil.
    redenen: list[str] = []
    niveau = NORMAAL
    if leeftijd >= drempels.vastloper_uren:
        niveau = VASTLOPER
        redenen.append(f"Ligt hier al {beschrijf_duur(leeftijd)} en is nog niet compleet.")
    elif stilstand >= drempels.stilstand_uren:
        niveau = VASTLOPER
        redenen.append(f"Geen voortgang in {beschrijf_duur(stilstand)}.")
    elif leeftijd >= drempels.letop_uren:
        niveau = LET_OP
        redenen.append(f"Al {beschrijf_duur(leeftijd)} incompleet.")

    if niveau != NORMAAL and status.ontbrekend:
        ontbreekt = ", ".join(
            f"{r.omschrijving} ({r.aantal_in_ph}/{r.aantal_besteld})" for r in status.ontbrekend[:3]
        )
        redenen.append(f"Ontbreekt: {ontbreekt}")

    return signaal(niveau, *redenen)


def haltoestand(
    status: PhStatus | None,
    signaal: Signaal | None,
    controle: Controle | None,
) -> str:
    """De kleur van een vak op de plattegrond."""
    if status is None:
        return BESCHIKBAAR
    if signaal is not None and signaal.niveau in (VASTLOPER, LET_OP):
        return signaal.niveau
    if controle is not None and controle.is_akkoord:
        return AFGEMELD
    if controle is not None:
        return IN_CONTROLE
    if status.mag_uit_ph:
        return KLAAR
    # Toegewezen aan een order, maar nog niet compleet — ook als er nog niets ligt.
    return VULLEND
