"""Vastlopers: signalering in het overzicht, onderzoek openen en afronden."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import maak_app
from app.service import ControleFout, PhService


@pytest.fixture
def client(instellingen, srs, opslag) -> TestClient:
    koppeling = TestClient(maak_app(instellingen, srs=srs, opslag=opslag))
    koppeling.cookies.set("ph_medewerker", "Bas")
    return koppeling


def verouder(opslag, code: str, uren: float) -> None:
    """Zet de waarneming van een PH terug in de tijd."""
    moment = (datetime.now() - timedelta(hours=uren)).isoformat(timespec="seconds")
    opslag._verbinding.execute(
        "UPDATE ph_observaties SET eerst_gezien = ?, laatst_gewijzigd = ? WHERE ph_code = ?",
        (moment, moment, code),
    )
    opslag._verbinding.commit()


def test_lang_liggende_ph_wordt_vastloper(service: PhService, opslag):
    service.overzicht()  # eerste waarneming
    verouder(opslag, "G-PH.02", 30)

    vastlopers = {r.ph.code for r in service.vastlopers()}
    assert "G-PH.02" in vastlopers

    regel = next(r for r in service.overzicht() if r.ph.code == "G-PH.02")
    assert regel.signaal.is_vastloper
    assert regel.toestand == "vastloper"


def test_vastlopers_staan_bovenaan_het_overzicht(service: PhService, opslag):
    service.overzicht()
    verouder(opslag, "K-PH.06", 30)

    assert service.overzicht()[0].ph.code == "K-PH.06"


def test_voortgang_zet_de_stilstandklok_terug(service: PhService, opslag, srs):
    service.overzicht()
    verouder(opslag, "G-PH.02", 12)
    assert service.vastlopers()  # geen voortgang in 12 uur

    # Er wordt alsnog gepickt: de inhoud verandert.
    from dataclasses import replace

    ph = srs.haal_ph("G-PH.02")
    regels = list(ph.regels)
    regels[1] = replace(regels[1], aantal_in_ph=2)
    srs._phs["G-PH.02"] = replace(ph, regels=tuple(regels))

    regel = next(r for r in service.overzicht() if r.ph.code == "G-PH.02")
    assert regel.signaal.stilstand_uren < 1


def test_afwijking_zet_ph_in_onderzoek(service: PhService):
    service.start_controle("G-PH.01", "Bas")
    service.meld_afwijking("G-PH.01", "Vaas is gebroken", "Bas")

    onderzoek = service.opslag.open_onderzoek("G-PH.01")
    assert onderzoek is not None and onderzoek.is_open
    assert onderzoek.notitie == "Vaas is gebroken"

    regel = next(r for r in service.overzicht() if r.ph.code == "G-PH.01")
    assert regel.signaal.is_vastloper


def test_onderzoek_afronden_haalt_signaal_weg(service: PhService):
    service.start_onderzoek("K-PH.06", reden="Artikel onvindbaar", medewerker="Bas")
    assert any(r.ph.code == "K-PH.06" for r in service.vastlopers())

    service.rond_onderzoek_af("K-PH.06", oplossing="Artikel gevonden in bulk", medewerker="Bas")

    assert service.opslag.open_onderzoek("K-PH.06") is None
    assert not any(r.ph.code == "K-PH.06" for r in service.vastlopers())


def test_onderzoek_afronden_zonder_onderzoek(service: PhService):
    with pytest.raises(ControleFout):
        service.rond_onderzoek_af("G-PH.01", oplossing="niets", medewerker="Bas")


def test_mislukte_afmelding_opent_onderzoek(service: PhService, srs, monkeypatch):
    service.start_controle("G-PH.04", "Bas")
    service.scan_barcode("G-PH.04", "8719325500118")

    monkeypatch.setattr(
        srs, "meld_ph_gereed", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("SRS down"))
    )
    with pytest.raises(RuntimeError):
        service.geef_akkoord("G-PH.04", "Bas")

    onderzoek = service.opslag.open_onderzoek("G-PH.04")
    assert onderzoek is not None
    assert "SRS" in onderzoek.reden


def test_akkoord_rondt_lopend_onderzoek_af(service: PhService):
    service.start_onderzoek("G-PH.04", reden="Twijfel over aantal", medewerker="Bas")
    service.start_controle("G-PH.04", "Bas")
    service.scan_barcode("G-PH.04", "8719325500118")

    service.geef_akkoord("G-PH.04", "Bas")

    assert service.opslag.open_onderzoek("G-PH.04") is None


def test_teveel_in_ph_valt_op(service: PhService):
    regel = next(r for r in service.overzicht() if r.ph.code == "K-PH.09")
    assert regel.status.status == "teveel"
    assert regel.signaal.vraagt_aandacht


def test_hal_toont_vakken_en_lege_plekken(client: TestClient):
    antwoord = client.get("/hal")
    assert antwoord.status_code == 200
    assert "G-PH.01" in antwoord.text
    assert "G-PH.03" in antwoord.text  # gat in de nummering, dus leeg vak

    gegevens = client.get("/api/hal").json()["vakken"]
    assert gegevens["G-PH.01"]["toestand"] == "klaar"
    assert gegevens["G-PH.03"]["toestand"] == "beschikbaar"
    assert gegevens["G-PH.02"]["toestand"] == "vullend"


def test_hal_toont_vastloper_kleur(client: TestClient, opslag):
    client.get("/hal")
    verouder(opslag, "G-PH.02", 30)

    gegevens = client.get("/api/hal").json()["vakken"]
    assert gegevens["G-PH.02"]["toestand"] == "vastloper"


def test_onderzoek_via_de_web_ui(client: TestClient, opslag):
    client.post("/ph/K-PH.06/onderzoek", data={"reden": "Artikel zoek", "notitie": "gang G leeg"})
    assert opslag.open_onderzoek("K-PH.06") is not None

    tekst = client.get("/ph/K-PH.06").text
    assert "In onderzoek" in tekst

    client.post("/ph/K-PH.06/onderzoek/afronden", data={"oplossing": "gevonden in bulk"})
    assert opslag.open_onderzoek("K-PH.06") is None
