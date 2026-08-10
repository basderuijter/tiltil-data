"""Plattegrond van de pigeonhole-hal.

De indeling wordt afgeleid uit de PH-codes die SRS teruggeeft (G-PH.01,
K-PH.05, …): de codes worden gegroepeerd per wand en de gaten ertussen worden
opgevuld, zodat ook de lege vakken zichtbaar zijn. Wie de echte indeling wil
vastleggen, zet die in `config/hal.json`:

    {
      "kolommen": 10,
      "wanden": [
        {"naam": "Gang G", "prefix": "G-PH", "van": 1, "tot": 24, "cijfers": 2}
      ]
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CODE_PATROON = re.compile(r"^(?P<wand>.*?)(?P<scheiding>[.\-_ ]?)(?P<nummer>\d+)$")

STANDAARD_KOLOMMEN = 10


@dataclass
class Vak:
    code: str
    toestand: str = "beschikbaar"
    ph: object | None = None
    status: object | None = None
    signaal: object | None = None
    controle: object | None = None

    @property
    def nummer(self) -> str:
        match = CODE_PATROON.match(self.code)
        return match.group("nummer") if match else self.code


@dataclass
class Wand:
    naam: str
    kolommen: int = STANDAARD_KOLOMMEN
    vakken: list[Vak] = field(default_factory=list)

    @property
    def aantal(self) -> int:
        return len(self.vakken)


def _ontleed(code: str) -> tuple[str, str, int, int] | None:
    """Splits 'G-PH.01' in wand 'G-PH', scheidingsteken '.', nummer 1, breedte 2."""
    match = CODE_PATROON.match(code.strip())
    if not match:
        return None
    cijfers = match.group("nummer")
    return match.group("wand"), match.group("scheiding"), int(cijfers), len(cijfers)


def _maak_code(wand: str, scheiding: str, nummer: int, breedte: int) -> str:
    return f"{wand}{scheiding}{str(nummer).zfill(breedte)}"


def leid_indeling_af(codes: Iterable[str], kolommen: int = STANDAARD_KOLOMMEN) -> list[Wand]:
    """Bouw wanden uit de codes en vul de gaten op met lege vakken."""
    per_wand: dict[tuple[str, str, int], set[int]] = {}
    losse: list[str] = []

    for code in codes:
        ontleed = _ontleed(code)
        if ontleed is None:
            losse.append(code)
            continue
        wand, scheiding, nummer, breedte = ontleed
        per_wand.setdefault((wand, scheiding, breedte), set()).add(nummer)

    wanden: list[Wand] = []
    for (wand, scheiding, breedte), nummers in sorted(per_wand.items()):
        hoogste = max(nummers)
        laagste = min(1, min(nummers))  # meestal 1, tenzij de nummering bij 0 begint
        wanden.append(
            Wand(
                naam=wand,
                kolommen=kolommen,
                vakken=[
                    Vak(code=_maak_code(wand, scheiding, nummer, breedte))
                    for nummer in range(laagste, hoogste + 1)
                ],
            )
        )

    if losse:
        wanden.append(
            Wand(naam="Overig", kolommen=kolommen, vakken=[Vak(code=c) for c in sorted(losse)])
        )
    return wanden


def lees_indeling(bestand: Path | str) -> list[Wand] | None:
    """Lees een vastgelegde indeling; geeft None als het bestand er niet is."""
    pad = Path(bestand)
    if not pad.exists():
        return None

    gegevens = json.loads(pad.read_text(encoding="utf-8"))
    standaard_kolommen = int(gegevens.get("kolommen", STANDAARD_KOLOMMEN))
    wanden: list[Wand] = []

    for beschrijving in gegevens.get("wanden", []):
        codes: list[str] = list(beschrijving.get("vakken", []))
        if not codes and "prefix" in beschrijving:
            prefix = beschrijving["prefix"]
            scheiding = beschrijving.get("scheiding", ".")
            breedte = int(beschrijving.get("cijfers", 2))
            for nummer in range(int(beschrijving.get("van", 1)), int(beschrijving["tot"]) + 1):
                codes.append(_maak_code(prefix, scheiding, nummer, breedte))
        wanden.append(
            Wand(
                naam=beschrijving.get("naam", beschrijving.get("prefix", "Wand")),
                kolommen=int(beschrijving.get("kolommen", standaard_kolommen)),
                vakken=[Vak(code=code) for code in codes],
            )
        )
    return wanden or None


def bouw_indeling(
    codes: Iterable[str],
    bestand: Path | str | None = None,
    kolommen: int = STANDAARD_KOLOMMEN,
) -> list[Wand]:
    """Vastgelegde indeling als die er is, anders afgeleid uit de codes.

    Codes die SRS teruggeeft maar niet in de vastgelegde indeling staan, komen
    er alsnog bij — een order mag nooit onzichtbaar zijn omdat de plattegrond
    verouderd is.
    """
    codes = list(codes)
    wanden = lees_indeling(bestand) if bestand else None
    if wanden is None:
        return leid_indeling_af(codes, kolommen)

    bekend = {vak.code for wand in wanden for vak in wand.vakken}
    ontbrekend = [code for code in codes if code not in bekend]
    if ontbrekend:
        wanden.extend(leid_indeling_af(ontbrekend, kolommen))
    return wanden
