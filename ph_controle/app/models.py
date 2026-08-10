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
    """Regel binnen een lopende controle: wat is geteld en in welke staat."""

    barcode: str
    omschrijving: str
    aantal_verwacht: int
    aantal_geteld: int = 0
    conditie: Conditie = "goed"
    notitie: str = ""
    sku: str = ""

    @property
    def is_akkoord(self) -> bool:
        return self.aantal_geteld == self.aantal_verwacht and self.conditie == "goed"

    @property
    def verschil(self) -> int:
        return self.aantal_geteld - self.aantal_verwacht


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

    @property
    def is_akkoord(self) -> bool:
        return self.akkoord_op is not None

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
