"""Domeinmodellen voor de PH-controle.

Een PH is een verzamellocatie (G-PH / K-PH) waar de gepickte regels van
één webshoporder samenkomen. Zodra alle regels erin liggen mag de order
gecontroleerd, akkoord gegeven en uit de PH gehaald worden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Conditie = Literal["goed", "beschadigd", "verkeerd_artikel", "ontbreekt"]

CONDITIES: tuple[Conditie, ...] = ("goed", "beschadigd", "verkeerd_artikel", "ontbreekt")

CONDITIE_LABELS: dict[str, str] = {
    "goed": "Goed",
    "beschadigd": "Beschadigd",
    "verkeerd_artikel": "Verkeerd artikel",
    "ontbreekt": "Ontbreekt",
}


@dataclass(frozen=True)
class PhRegel:
    """Eén orderregel zoals SRS die kent, met wat er nu in de PH ligt."""

    barcode: str
    omschrijving: str
    aantal_besteld: int
    aantal_in_ph: int
    sku: str = ""
    picklocatie: str = ""

    @property
    def tekort(self) -> int:
        return max(0, self.aantal_besteld - self.aantal_in_ph)

    @property
    def teveel(self) -> int:
        return max(0, self.aantal_in_ph - self.aantal_besteld)


@dataclass(frozen=True)
class Ph:
    """Een PH-verzamellocatie met de order die erin verzameld wordt."""

    code: str
    order_referentie: str
    regels: tuple[PhRegel, ...] = ()
    order_id: str = ""
    klant: str = ""
    adres: str = ""
    postcode_plaats: str = ""
    land: str = "Nederland"
    verzendmethode: str = ""
    opmerking: str = ""
    orderdatum: str = ""
    srs_status: str = ""

    def regel(self, barcode: str) -> PhRegel | None:
        for r in self.regels:
            if r.barcode == barcode:
                return r
        return None


@dataclass
class ControleRegel:
    """Regel binnen een lopende controle: wat is geteld en in welke staat.

    `per_krat` houdt bij hoeveel stuks in welke krat gingen (index 0 = krat 1).
    Eén krat is één doos, dus dit is precies wat Sendcloud per pakket wil weten
    en wat op de pakbon van díe doos hoort te staan.
    """

    barcode: str
    omschrijving: str
    aantal_verwacht: int
    aantal_geteld: int = 0
    conditie: Conditie = "goed"
    notitie: str = ""
    sku: str = ""
    per_krat: list[int] = field(default_factory=list)

    @property
    def is_akkoord(self) -> bool:
        return self.aantal_geteld == self.aantal_verwacht and self.conditie == "goed"

    @property
    def verschil(self) -> int:
        return self.aantal_geteld - self.aantal_verwacht

    def aantal_in_krat(self, krat: int) -> int:
        return self.per_krat[krat - 1] if 0 < krat <= len(self.per_krat) else 0

    def tel_in_krat(self, krat: int, erbij: int = 1) -> None:
        while len(self.per_krat) < krat:
            self.per_krat.append(0)
        self.per_krat[krat - 1] = max(0, self.per_krat[krat - 1] + erbij)

    def herverdeel(self, krat: int) -> None:
        """Zorg dat de kratten samen precies het getelde aantal bevatten.

        Nodig na een handmatige correctie: het verschil gaat naar (of af van)
        de krat waar de medewerker mee bezig is.
        """
        while len(self.per_krat) < krat:
            self.per_krat.append(0)
        verschil = self.aantal_geteld - sum(self.per_krat)
        if verschil > 0:
            self.per_krat[krat - 1] += verschil
            return
        # Te veel toegewezen: haal eraf, te beginnen bij de actieve krat.
        tekort = -verschil
        volgorde = [krat - 1] + [i for i in range(len(self.per_krat)) if i != krat - 1]
        for index in volgorde:
            if tekort <= 0:
                break
            eraf = min(self.per_krat[index], tekort)
            self.per_krat[index] -= eraf
            tekort -= eraf


@dataclass
class Observatie:
    """Wat de app door de tijd heen van een PH heeft gezien.

    SRS geeft geen historie mee, dus houden we zelf bij sinds wanneer een PH
    gevuld is en wanneer er voor het laatst iets veranderde. Daarmee zien we
    of een order stilligt.
    """

    ph_code: str
    eerst_gezien: datetime
    laatst_gewijzigd: datetime
    vingerafdruk: str = ""
    compleet_sinds: datetime | None = None
    order_referentie: str = ""


@dataclass
class Onderzoek:
    """Een PH die uitgezocht moet worden, met wie het oppakt en de afloop."""

    ph_code: str
    reden: str
    geopend_op: datetime
    geopend_door: str = ""
    notitie: str = ""
    opgelost_op: datetime | None = None
    opgelost_door: str = ""
    oplossing: str = ""
    id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.opgelost_op is None


@dataclass
class Controle:
    """Controlesessie van één PH door één medewerker."""

    ph_code: str
    order_referentie: str
    regels: list[ControleRegel] = field(default_factory=list)
    medewerker: str = ""
    gestart_op: datetime | None = None
    akkoord_op: datetime | None = None
    akkoord_door: str = ""
    afwijking_notitie: str = ""
    id: int | None = None

    # Kratten: één krat is één doos is één label.
    kratten: int = 1
    actieve_krat: int = 1

    # Deellevering: alleen wat er nu ligt gaat mee, met een eigen referentie
    # (WEB-1042-1, WEB-1042-2) zodat de rest later een eigen levering wordt.
    is_deellevering: bool = False
    levering_referentie: str = ""

    # Verzending
    zending_methode: str = ""
    zending_vervoerder: str = ""
    tracking: list[str] = field(default_factory=list)
    labels_geprint: bool = False
    label_fout: str = ""
    kratverdeling_exact: bool = True
    giftcards_overgeslagen: int = 0

    @property
    def is_akkoord(self) -> bool:
        return self.akkoord_op is not None

    @property
    def referentie(self) -> str:
        """Wat er op de pakbon en in Sendcloud staat."""
        return self.levering_referentie or self.order_referentie

    @property
    def heeft_label(self) -> bool:
        return bool(self.tracking) or self.labels_geprint

    def krat_regels(self, krat: int) -> list[ControleRegel]:
        """De regels die in deze doos zitten, met het aantal van díe doos."""
        return [r for r in self.regels if r.aantal_in_krat(krat) > 0]

    def regel(self, barcode: str) -> ControleRegel | None:
        for r in self.regels:
            if r.barcode == barcode:
                return r
        return None

    @property
    def totaal_verwacht(self) -> int:
        return sum(r.aantal_verwacht for r in self.regels)

    @property
    def totaal_geteld(self) -> int:
        return sum(r.aantal_geteld for r in self.regels)
