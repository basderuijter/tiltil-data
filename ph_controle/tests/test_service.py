from __future__ import annotations

import pytest

from app.service import ControleFout, PhService
from app.srs.base import PhNietGevonden


def tel_alles(service: PhService, code: str) -> None:
    ph = service.haal_ph(code)
    for regel in ph.regels:
        for _ in range(regel.aantal_besteld):
            service.scan_barcode(code, regel.barcode)


def test_overzicht_zet_werk_bovenaan(service: PhService):
    codes = [r.ph.code for r in service.overzicht()]
    statussen = {r.ph.code: r.status.mag_uit_ph for r in service.overzicht()}

    assert statussen["G-PH.01"] is True
    assert statussen["G-PH.02"] is False
    # Complete PH's zonder controle staan vooraan.
    assert codes.index("G-PH.01") < codes.index("G-PH.02")


def test_controle_hervat_dezelfde_sessie(service: PhService):
    eerste = service.start_controle("G-PH.01", "Bas")
    service.scan_barcode("G-PH.01", "8719325104871")
    tweede = service.start_controle("G-PH.01", "Bas")

    assert eerste.id == tweede.id
    assert tweede.regel("8719325104871").aantal_geteld == 1


def test_volledige_gang_van_scan_tot_akkoord(service: PhService, srs):
    service.start_controle("G-PH.01", "Bas")
    tel_alles(service, "G-PH.01")

    resultaat = service.geef_akkoord("G-PH.01", "Bas")

    assert resultaat.controle.is_akkoord
    assert resultaat.srs_referentie.startswith("MOCK-G-PH.01")
    assert srs.afmeldingen[0]["order_referentie"] == "WEB-104872"
    assert srs.haal_ph("G-PH.01").srs_status == "afgemeld"

    ph, controle = service.pakbon_gegevens("G-PH.01")
    assert controle.totaal_geteld == 4
    assert ph.klant == "Marieke de Groot"


def test_akkoord_geblokkeerd_bij_onvolledige_telling(service: PhService):
    service.start_controle("G-PH.01", "Bas")
    service.scan_barcode("G-PH.01", "8719325104871")

    with pytest.raises(ControleFout) as fout:
        service.geef_akkoord("G-PH.01", "Bas")
    assert "Akkoord kan nog niet" in str(fout.value)


def test_akkoord_geblokkeerd_bij_beschadigd_product(service: PhService):
    service.start_controle("G-PH.01", "Bas")
    tel_alles(service, "G-PH.01")
    service.zet_regel("G-PH.01", "8719325104888", conditie="beschadigd", notitie="deukje")

    with pytest.raises(ControleFout):
        service.geef_akkoord("G-PH.01", "Bas")


def test_akkoord_vereist_naam(service: PhService):
    service.start_controle("G-PH.01")
    tel_alles(service, "G-PH.01")

    with pytest.raises(ControleFout):
        service.geef_akkoord("G-PH.01", "  ")


def test_afmelding_faalt_dan_blijft_controle_open(service: PhService, srs, monkeypatch):
    service.start_controle("G-PH.01", "Bas")
    tel_alles(service, "G-PH.01")

    def stuk(*_args, **_kwargs):
        raise RuntimeError("SRS down")

    monkeypatch.setattr(srs, "meld_ph_gereed", stuk)
    with pytest.raises(RuntimeError):
        service.geef_akkoord("G-PH.01", "Bas")

    controle = service.opslag.open_controle("G-PH.01")
    assert controle is not None and not controle.is_akkoord
    with pytest.raises(ControleFout):
        service.pakbon_gegevens("G-PH.01")


def test_zet_regel_weigert_meer_dan_besteld(service: PhService):
    service.start_controle("G-PH.01", "Bas")
    with pytest.raises(ControleFout):
        service.zet_regel("G-PH.01", "8719325104888", aantal=5)


def test_afwijking_melden_houdt_ph_open(service: PhService):
    service.start_controle("G-PH.02", "Bas")
    controle = service.meld_afwijking("G-PH.02", "Ketting ligt niet in de PH", "Bas")

    assert controle.afwijking_notitie == "Ketting ligt niet in de PH"
    assert service.opslag.open_controle("G-PH.02") is not None
    soorten = [g["soort"] for g in service.opslag.gebeurtenissen("G-PH.02")]
    assert "afwijking_gemeld" in soorten


def test_onbekende_ph(service: PhService):
    with pytest.raises(PhNietGevonden):
        service.haal_ph("G-PH.99")
