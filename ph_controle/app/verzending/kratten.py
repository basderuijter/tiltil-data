"""Kratten omzetten naar de pakketverdeling die Sendcloud wil zien.

Bij multicollo eist Sendcloud per pakket een `parcel_items`-lijst. De losse
scan-app wist niet wat er in welke doos zat en verdeelde de regels daarom
willekeurig (`split_items`). De PH-controle scant elk artikel toch al, dus
daar is de echte verdeling bekend: één krat is één doos.

Wat niet vanzelf gaat: Sendcloud kent zijn eigen `item_id` per orderregel en
wij scannen barcodes. Die twee worden hier op naam gekoppeld. Lukt dat niet
volledig, dan valt de verdeling terug op `split_items` — arbitrair, maar de
totalen kloppen, en dat is wat de vervoerder nodig heeft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import OrderItem
from .sendcloud import split_items


@dataclass
class KratRegel:
    """Eén artikel in een krat, zoals de PH-controle het geteld heeft."""

    omschrijving: str
    aantal: int
    barcode: str = ""
    sku: str = ""


@dataclass
class Krat:
    nummer: int
    regels: list[KratRegel] = field(default_factory=list)

    @property
    def aantal_stuks(self) -> int:
        return sum(r.aantal for r in self.regels)


def _normaliseer(tekst: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (tekst or "").lower()).strip()


def is_giftcard(regel: KratRegel, patroon: str) -> bool:
    """Digitale cadeaubonnen horen niet in een doos."""
    if not patroon:
        return False
    tekst = f"{regel.omschrijving} {regel.sku} {regel.barcode}".lower()
    return re.search(patroon, tekst) is not None


def zonder_giftcards(kratten: list[Krat], patroon: str) -> list[Krat]:
    """Kratten zonder de digitale regels; lege kratten vallen weg."""
    overgebleven: list[Krat] = []
    for krat in kratten:
        regels = [r for r in krat.regels if not is_giftcard(r, patroon)]
        if regels:
            overgebleven.append(Krat(nummer=krat.nummer, regels=regels))
    return overgebleven


def _koppel_op_naam(
    kratten: list[Krat], order_items: list[OrderItem]
) -> list[dict[str, Any]] | None:
    """Verdeel de item_id's van Sendcloud over de kratten, op naam gematcht.

    Geeft None terug zodra één regel niet te plaatsen is; dan is de verdeling
    onbetrouwbaar en kan de aanroeper beter terugvallen.
    """
    voorraad: list[tuple[str, str, int]] = [
        (item.item_id, _normaliseer(item.name), int(item.quantity or 1))
        for item in order_items
        if item.item_id
    ]
    if not voorraad:
        return None

    resterend = {index: aantal for index, (_, _, aantal) in enumerate(voorraad)}
    pakketten: list[dict[str, int]] = []

    for krat in kratten:
        inhoud: dict[str, int] = {}
        for regel in krat.regels:
            nodig = regel.aantal
            gezocht = _normaliseer(regel.omschrijving)
            for kandidaat in (_gelijk, _bevat):
                for index, (item_id, naam, _) in enumerate(voorraad):
                    if nodig <= 0:
                        break
                    if resterend[index] <= 0 or not kandidaat(gezocht, naam):
                        continue
                    neem = min(nodig, resterend[index])
                    inhoud[item_id] = inhoud.get(item_id, 0) + neem
                    resterend[index] -= neem
                    nodig -= neem
                if nodig <= 0:
                    break
            if nodig > 0:
                return None
        pakketten.append(inhoud)

    return [
        {
            "parcel_items": [
                {"item_id": item_id, "quantity": aantal} for item_id, aantal in inhoud.items()
            ]
        }
        for inhoud in pakketten
    ]


def _gelijk(gezocht: str, naam: str) -> bool:
    return bool(gezocht) and gezocht == naam


def _bevat(gezocht: str, naam: str) -> bool:
    return bool(gezocht) and bool(naam) and (gezocht in naam or naam in gezocht)


@dataclass
class Verdeling:
    pakketten: list[dict[str, Any]]
    # False als er teruggevallen is op de willekeurige verdeling; dan klopt het
    # totaal wel, maar staat niet per doos het juiste artikel.
    exact: bool = True


def verdeel(kratten: list[Krat], order_items: list[OrderItem]) -> Verdeling:
    """De `parcels`-lijst voor een multicollo-aanmelding."""
    gekoppeld = _koppel_op_naam(kratten, order_items)
    if gekoppeld is not None:
        return Verdeling(pakketten=gekoppeld, exact=True)
    return Verdeling(pakketten=split_items(order_items, len(kratten)), exact=False)
