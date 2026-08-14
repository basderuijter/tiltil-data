"""Mock-SRS op basis van een JSON-fixture.

Bedoeld om de app te draaien en te oefenen zonder SRS-koppeling, en om de
tests deterministisch te houden. Afmeldingen worden alleen in het geheugen
bijgehouden.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ..models import Controle, Ph, PhRegel
from .base import PhNietGevonden


def _ph_uit_dict(data: dict) -> Ph:
    return Ph(
        code=data["code"],
        order_referentie=data.get("order_referentie", ""),
        order_id=str(data.get("order_id", "")),
        klant=data.get("klant", ""),
        adres=data.get("adres", ""),
        postcode_plaats=data.get("postcode_plaats", ""),
        land=data.get("land", "Nederland"),
        verzendmethode=data.get("verzendmethode", ""),
        opmerking=data.get("opmerking", ""),
        orderdatum=data.get("orderdatum", ""),
        srs_status=data.get("srs_status", "open"),
        regels=tuple(
            PhRegel(
                barcode=str(r["barcode"]),
                omschrijving=r.get("omschrijving", ""),
                aantal_besteld=int(r.get("aantal_besteld", 0)),
                aantal_in_ph=int(r.get("aantal_in_ph", 0)),
                sku=str(r.get("sku", "")),
                picklocatie=r.get("picklocatie", ""),
            )
            for r in data.get("regels", [])
        ),
    )


class MockSrsClient:
    """Leest PH's uit een JSON-bestand; afmeldingen blijven in het geheugen."""

    def __init__(self, fixture: Path | str):
        self.fixture = Path(fixture)
        self._phs: dict[str, Ph] = {}
        self.afmeldingen: list[dict] = []
        self._laad()

    def _laad(self) -> None:
        data = json.loads(self.fixture.read_text(encoding="utf-8"))
        self._phs = {ph["code"]: _ph_uit_dict(ph) for ph in data.get("phs", [])}

    def lijst_phs(self) -> list[Ph]:
        return sorted(self._phs.values(), key=lambda p: p.code)

    def haal_ph(self, code: str) -> Ph:
        try:
            return self._phs[code]
        except KeyError as fout:
            raise PhNietGevonden(f"PH {code} bestaat niet in SRS.") from fout

    def meld_ph_gereed(self, ph: Ph, controle: Controle) -> str:
        referentie = f"MOCK-{ph.code}-{len(self.afmeldingen) + 1}"
        self.afmeldingen.append(
            {
                "ph": ph.code,
                "order_referentie": ph.order_referentie,
                "medewerker": controle.akkoord_door or controle.medewerker,
                "regels": [
                    {"barcode": r.barcode, "aantal": r.aantal_geteld} for r in controle.regels
                ],
                "referentie": referentie,
            }
        )
        self._phs[ph.code] = replace(self._phs[ph.code], srs_status="afgemeld")
        return referentie
