"""Tests voor de snelheidsingrepen: scan-to-open, snelmodus, doorstroom."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import maak_app
from app.service import PhService


@pytest.fixture
def client(instellingen, srs, opslag) -> TestClient:
    koppeling = TestClient(maak_app(instellingen, srs=srs, opslag=opslag))
    koppeling.cookies.set("ph_medewerker", "Bas")
    return koppeling


def test_zoeken_op_ph_code_order_en_artikel(service: PhService):
    assert service.zoek("G-PH.01") == "G-PH.01"
    assert service.zoek("g-ph.01") == "G-PH.01"
    assert service.zoek("WEB-104915") == "K-PH.05"
    assert service.zoek("8719325104871") == "G-PH.01"  # barcode zit in één PH
    assert service.zoek("") is None
    assert service.zoek("bestaat-niet") is None


def test_artikel_in_meerdere_phs_geeft_geen_gok(service: PhService):
    # Kussenhoes ligt zowel in K-PH.05 als in K-PH.09.
    assert service.zoek("8719325301129") is None


def test_scan_to_open_stuurt_door_naar_de_ph(client: TestClient):
    antwoord = client.post(
        "/open", data={"zoekterm": "WEB-104872", "terug": "/"}, follow_redirects=False
    )
    assert antwoord.status_code == 303
    assert antwoord.headers["location"] == "/ph/G-PH.01"


def test_scan_to_open_meldt_als_er_niets_gevonden_is(client: TestClient):
    antwoord = client.post(
        "/open", data={"zoekterm": "9999", "terug": "/"}, follow_redirects=False
    )
    assert antwoord.status_code == 303
    assert "melding=" in antwoord.headers["location"]


def test_openen_van_complete_ph_start_de_controle_meteen(client: TestClient, opslag):
    tekst = client.get("/ph/G-PH.01").text

    assert opslag.open_controle("G-PH.01") is not None
    assert "Scannen" in tekst  # scanscherm staat er direct, zonder extra klik


def test_incomplete_ph_start_niet_vanzelf(client: TestClient, opslag):
    client.get("/ph/G-PH.02")
    assert opslag.open_controle("G-PH.02") is None


def test_akkoord_via_api_geeft_pakbon_en_volgende_ph(client: TestClient, srs):
    client.get("/ph/G-PH.04")  # enkelstuksorder, controle start vanzelf
    client.post("/api/ph/G-PH.04/scan", json={"barcode": "8719325500118"})

    antwoord = client.post("/api/ph/G-PH.04/akkoord")
    gegevens = antwoord.json()

    assert antwoord.status_code == 200
    assert gegevens["pakbon_url"] == "/ph/G-PH.04/pakbon?direct=1"
    assert gegevens["volgende_ph"] in ("G-PH.01", "K-PH.05")
    assert srs.afmeldingen[0]["ph"] == "G-PH.04"


def test_akkoord_via_api_weigert_onvolledige_controle(client: TestClient):
    client.get("/ph/G-PH.01")
    antwoord = client.post("/api/ph/G-PH.01/akkoord")

    assert antwoord.status_code == 409
    assert "Akkoord kan nog niet" in antwoord.json()["fout"]


def test_volgende_klaar_slaat_lopende_en_afgeronde_over(service: PhService):
    eerste = service.volgende_klaar()
    assert eerste in ("G-PH.01", "G-PH.04", "K-PH.05")

    service.start_controle(eerste, "Bas")
    assert service.volgende_klaar() != eerste


def test_doorlooptijd_wordt_vastgelegd(service: PhService):
    service.start_controle("G-PH.04", "Bas")
    service.scan_barcode("G-PH.04", "8719325500118")

    resultaat = service.geef_akkoord("G-PH.04", "Bas")

    assert resultaat.doorlooptijd_seconden >= 0
    assert service.opslag.controletijden(resultaat.controle.gestart_op)
