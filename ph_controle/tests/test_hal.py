from __future__ import annotations

import json

from app.hal import bouw_indeling, leid_indeling_af, lees_indeling


def codes_van(wanden) -> dict[str, list[str]]:
    return {wand.naam: [vak.code for vak in wand.vakken] for wand in wanden}


def test_indeling_afgeleid_uit_codes_vult_gaten():
    wanden = leid_indeling_af(["G-PH.01", "G-PH.04", "K-PH.02"])
    kaart = codes_van(wanden)

    assert kaart["G-PH"] == ["G-PH.01", "G-PH.02", "G-PH.03", "G-PH.04"]
    assert kaart["K-PH"] == ["K-PH.01", "K-PH.02"]


def test_codes_zonder_nummer_komen_bij_overig():
    kaart = codes_van(leid_indeling_af(["G-PH.01", "RETOUR"]))
    assert kaart["Overig"] == ["RETOUR"]


def test_vastgelegde_indeling_wint(tmp_path):
    bestand = tmp_path / "hal.json"
    bestand.write_text(
        json.dumps(
            {
                "kolommen": 6,
                "wanden": [{"naam": "Gang G", "prefix": "G-PH", "van": 1, "tot": 3}],
            }
        ),
        encoding="utf-8",
    )

    wanden = lees_indeling(bestand)
    assert codes_van(wanden) == {"Gang G": ["G-PH.01", "G-PH.02", "G-PH.03"]}
    assert wanden[0].kolommen == 6


def test_onbekende_ph_uit_srs_wordt_toegevoegd(tmp_path):
    bestand = tmp_path / "hal.json"
    bestand.write_text(
        json.dumps({"wanden": [{"naam": "Gang G", "prefix": "G-PH", "van": 1, "tot": 2}]}),
        encoding="utf-8",
    )

    kaart = codes_van(bouw_indeling(["G-PH.01", "X-PH.03"], bestand))

    assert kaart["Gang G"] == ["G-PH.01", "G-PH.02"]
    assert kaart["X-PH"] == ["X-PH.01", "X-PH.02", "X-PH.03"]


def test_zonder_bestand_wordt_afgeleid(tmp_path):
    kaart = codes_van(bouw_indeling(["K-PH.02"], tmp_path / "bestaat-niet.json"))
    assert kaart == {"K-PH": ["K-PH.01", "K-PH.02"]}
