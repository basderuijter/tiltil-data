from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import maak_app


@pytest.fixture
def client(instellingen, srs, opslag) -> TestClient:
    return TestClient(maak_app(instellingen, srs=srs, opslag=opslag))


def test_overzicht_toont_phs(client: TestClient):
    antwoord = client.get("/")
    assert antwoord.status_code == 200
    assert "G-PH.01" in antwoord.text
    assert "Incompleet" in antwoord.text


def test_incomplete_ph_waarschuwt(client: TestClient):
    tekst = client.get("/ph/G-PH.02").text
    assert "nog niet compleet" in tekst
    assert "Ketting goudkleurig 45cm" in tekst


def test_scannen_en_akkoord_leidt_naar_pakbon(client: TestClient, srs):
    client.post("/ph/G-PH.01/start", data={"naam": "Bas"})

    for barcode, aantal in [
        ("8719325104871", 2),
        ("8719325104888", 1),
        ("8719325104895", 1),
    ]:
        for _ in range(aantal):
            antwoord = client.post("/api/ph/G-PH.01/scan", json={"barcode": barcode})
            assert antwoord.status_code == 200
            assert antwoord.json()["scan"]["status"] == "geteld"

    assert antwoord.json()["controle"]["mag_akkoord"] is True

    akkoord = client.post("/ph/G-PH.01/akkoord", data={"naam": "Bas"}, follow_redirects=False)
    assert akkoord.status_code == 303
    assert akkoord.headers["location"] == "/ph/G-PH.01/pakbon?direct=1"
    assert srs.afmeldingen and srs.afmeldingen[0]["medewerker"] == "Bas"

    pakbon = client.get("/ph/G-PH.01/pakbon")
    assert pakbon.status_code == 200
    assert "Marieke de Groot" in pakbon.text
    assert "Vaas geribbeld zand M" in pakbon.text


def test_scan_van_vreemde_barcode_geeft_melding(client: TestClient):
    client.post("/ph/G-PH.01/start", data={"naam": "Bas"})
    antwoord = client.post("/api/ph/G-PH.01/scan", json={"barcode": "0000000000000"})

    assert antwoord.status_code == 200
    assert antwoord.json()["scan"]["status"] == "onbekend"
    assert antwoord.json()["controle"]["totaal_geteld"] == 0


def test_akkoord_zonder_volledige_telling_wordt_geweigerd(client: TestClient):
    client.post("/ph/G-PH.01/start", data={"naam": "Bas"})
    antwoord = client.post("/ph/G-PH.01/akkoord", data={"naam": "Bas"})

    assert antwoord.status_code == 409
    assert "Akkoord kan nog niet" in antwoord.text


def test_pakbon_zonder_akkoord_bestaat_niet(client: TestClient):
    antwoord = client.get("/ph/G-PH.01/pakbon")
    assert antwoord.status_code == 409


def test_onbekende_ph_geeft_404(client: TestClient):
    assert client.get("/ph/ONZIN").status_code == 404


def test_gezondheid(client: TestClient):
    gegevens = client.get("/gezondheid").json()
    assert gegevens["srs"] == "ok"
    assert gegevens["aantal_phs"] == 4


def test_scan_zonder_lopende_controle(client: TestClient):
    antwoord = client.post("/api/ph/G-PH.01/scan", json={"barcode": "8719325104871"})
    assert antwoord.status_code == 409
