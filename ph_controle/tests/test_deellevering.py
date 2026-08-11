"""Deelleveringen: wat er ligt kan weg, de rest volgt later."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.completeness import ph_status
from app.service import ControleFout, PhService


def test_incomplete_ph_mag_als_deellevering_weg(service: PhService):
    status = ph_status(service.haal_ph("G-PH.02"))

    assert not status.mag_uit_ph  # de hele order nog niet
    assert status.mag_deellevering  # wat er ligt wel


def test_lege_ph_biedt_geen_deellevering(service: PhService):
    assert not ph_status(service.haal_ph("K-PH.06")).mag_deellevering


def test_deellevering_krijgt_eigen_referentie(service: PhService):
    controle = service.start_controle("G-PH.02", "Bas", deellevering=True)

    assert controle.is_deellevering
    assert controle.levering_referentie == "WEB-104903-1"
    assert controle.referentie == "WEB-104903-1"


def test_akkoord_op_wat_er_ligt(service: PhService, srs):
    service.start_controle("G-PH.02", "Bas", deellevering=True)
    service.scan_barcode("G-PH.02", "8719325200114")  # trui, 1 van 1
    service.scan_barcode("G-PH.02", "8719325200121")  # broek, 1 van 2

    resultaat = service.geef_akkoord("G-PH.02", "Bas")

    assert resultaat.controle.is_akkoord
    # SRS krijgt alleen wat er echt meegaat.
    afgemeld = {r["barcode"]: r["aantal"] for r in srs.afmeldingen[0]["regels"]}
    assert afgemeld["8719325200114"] == 1
    assert afgemeld["8719325200121"] == 1
    assert afgemeld["8719325200138"] == 0


def test_deellevering_zonder_scans_kan_niet(service: PhService):
    service.start_controle("G-PH.02", "Bas", deellevering=True)

    with pytest.raises(ControleFout):
        service.geef_akkoord("G-PH.02", "Bas")


def test_beschadigd_artikel_blokkeert_ook_een_deellevering(service: PhService):
    service.start_controle("G-PH.02", "Bas", deellevering=True)
    service.scan_barcode("G-PH.02", "8719325200114")
    service.zet_regel("G-PH.02", "8719325200114", conditie="beschadigd")

    with pytest.raises(ControleFout):
        service.geef_akkoord("G-PH.02", "Bas")


def test_volgende_levering_telt_door(service: PhService, srs):
    service.start_controle("G-PH.02", "Bas", deellevering=True)
    service.scan_barcode("G-PH.02", "8719325200114")
    service.geef_akkoord("G-PH.02", "Bas")

    openstaand = {r["barcode"]: r["aantal"] for r in service.nog_te_leveren("G-PH.02")}
    assert openstaand == {"8719325200121": 2, "8719325200138": 1}

    # De rest komt binnen; de tweede levering krijgt zijn eigen referentie.
    ph = srs.haal_ph("G-PH.02")
    srs._phs["G-PH.02"] = replace(
        ph, regels=tuple(replace(r, aantal_in_ph=r.aantal_besteld) for r in ph.regels)
    )
    tweede = service.start_controle("G-PH.02", "Bas")

    assert tweede.levering_referentie == "WEB-104903-2"


def test_pakbon_van_een_deellevering_noemt_wat_er_nog_volgt(service: PhService):
    service.start_controle("G-PH.02", "Bas", deellevering=True)
    service.scan_barcode("G-PH.02", "8719325200114")
    service.geef_akkoord("G-PH.02", "Bas")

    _ph, controle, kratten = service.pakbon_gegevens("G-PH.02")

    assert controle.is_deellevering
    assert controle.referentie == "WEB-104903-1"
    assert [r["omschrijving"] for r in kratten[0]["regels"]] == ["Trui katoen taupe M"]
    assert service.nog_te_leveren("G-PH.02")
