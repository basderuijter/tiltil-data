"""Verzending: kratverdeling, giftcards, labels en opnieuw printen."""

from __future__ import annotations

import pytest

from app.service import ControleFout, PhService
from app.verzending import Adres, Krat, KratRegel, Verzendservice
from app.verzending.config import VerzendFout, VerzendInstellingen
from app.verzending.kratten import is_giftcard, verdeel, zonder_giftcards
from app.verzending.models import OrderItem


# -- instellingen ---------------------------------------------------------


def test_onmogelijke_dpi_wordt_bij_opstarten_geweigerd():
    with pytest.raises(VerzendFout):
        VerzendInstellingen(label_mime_type="application/pdf", label_dpi=300)
    with pytest.raises(VerzendFout):
        VerzendInstellingen(label_mime_type="image/png", label_dpi=72)
    # ZPL negeert de dpi, dus daar mag alles.
    VerzendInstellingen(label_mime_type="application/zpl", label_dpi=203)


def test_onbekende_printbackend_wordt_geweigerd():
    with pytest.raises(VerzendFout):
        VerzendInstellingen(print_backend="magie")


# -- kratverdeling --------------------------------------------------------


def krat(nummer: int, *regels: tuple[str, int]) -> Krat:
    return Krat(nummer=nummer, regels=[KratRegel(omschrijving=n, aantal=a) for n, a in regels])


def test_kratten_worden_op_naam_aan_sendcloud_regels_gekoppeld():
    order_items = [
        OrderItem(item_id="5552", quantity=2, name="Cylinder candle"),
        OrderItem(item_id="5555", quantity=1, name="Linnen kussen"),
    ]
    verdeling = verdeel(
        [krat(1, ("Cylinder candle", 2)), krat(2, ("Linnen kussen", 1))], order_items
    )

    assert verdeling.exact
    assert verdeling.pakketten == [
        {"parcel_items": [{"item_id": "5552", "quantity": 2}]},
        {"parcel_items": [{"item_id": "5555", "quantity": 1}]},
    ]


def test_verdeling_valt_terug_als_de_namen_niet_matchen():
    order_items = [OrderItem(item_id="5552", quantity=2, name="Cylinder candle")]

    verdeling = verdeel([krat(1, ("Iets heel anders", 1)), krat(2, ("Nog iets", 1))], order_items)

    assert not verdeling.exact
    # Terugval verdeelt wel alles: de totalen kloppen voor de vervoerder.
    assert len(verdeling.pakketten) == 2


def test_giftcards_gaan_niet_in_een_doos():
    patroon = r"giftcard|cadeaubon"
    assert is_giftcard(KratRegel(omschrijving="Digitale giftcard", aantal=1), patroon)
    assert is_giftcard(KratRegel(omschrijving="Bon", aantal=1, sku="CADEAUBON-25"), patroon)
    assert not is_giftcard(KratRegel(omschrijving="Vaas geribbeld", aantal=1), patroon)

    kratten = [krat(1, ("Vaas", 1), ("Digitale giftcard", 1)), krat(2, ("Giftcard 50", 1))]
    over = zonder_giftcards(kratten, patroon)

    assert len(over) == 1  # krat 2 was alleen digitaal en valt weg
    assert [r.omschrijving for r in over[0].regels] == ["Vaas"]


# -- verzendservice in demomodus -----------------------------------------


@pytest.fixture
def verzending(tmp_path) -> Verzendservice:
    return Verzendservice(
        VerzendInstellingen(demo_mode=True, print_backend="none", spool_dir=tmp_path / "spool")
    )


def test_een_label_per_krat(verzending: Verzendservice):
    resultaat = verzending.maak_labels(
        "WEB-1042", [krat(1, ("Vaas", 1)), krat(2, ("Plaid", 1))]
    )

    assert len(resultaat.labels) == 2
    assert len(resultaat.tracking) == 2
    assert resultaat.geprint


def test_order_met_alleen_giftcards_krijgt_geen_label(verzending: Verzendservice):
    resultaat = verzending.maak_labels("WEB-1042", [krat(1, ("Digitale giftcard", 2))])

    assert resultaat.labels == []
    assert resultaat.overgeslagen_giftcards == 2


def test_losse_zending_zonder_ph(verzending: Verzendservice):
    resultaat = verzending.losse_zending(
        Adres(naam="Klantenservice", adres="Dorpsstraat 1", postcode="1234 AB", plaats="Oss"),
        methode_code="postnl:standard",
        aantal=2,
        referentie="retour-889",
    )

    assert len(resultaat.labels) == 2
    assert resultaat.vervoerder == "postnl"


# -- PH-controle met kratten en labels ------------------------------------


@pytest.fixture
def dienst(srs, opslag, instellingen, verzending) -> PhService:
    return PhService(srs, opslag, instellingen, verzending)


def test_scannen_verdeelt_over_kratten(dienst: PhService):
    dienst.start_controle("G-PH.01", "Bas")
    dienst.scan_barcode("G-PH.01", "8719325104871")

    dienst.zet_krat("G-PH.01", 2)
    dienst.scan_barcode("G-PH.01", "8719325104871")
    dienst.scan_barcode("G-PH.01", "8719325104888")

    controle = dienst.opslag.open_controle("G-PH.01")
    assert controle.kratten == 2
    assert controle.regel("8719325104871").per_krat == [1, 1]
    assert [r.omschrijving for r in controle.krat_regels(2)] == [
        "Vaas geribbeld zand M",
        "Geurkaars vijg 220gr",
    ]


def test_krat_overslaan_kan_niet(dienst: PhService):
    dienst.start_controle("G-PH.01", "Bas")
    with pytest.raises(ControleFout):
        dienst.zet_krat("G-PH.01", 3)


def test_lege_krat_kost_geen_label(dienst: PhService):
    dienst.start_controle("G-PH.04", "Bas")
    dienst.zet_krat("G-PH.04", 2)  # per ongeluk een krat erbij
    dienst.zet_krat("G-PH.04", 1)
    dienst.scan_barcode("G-PH.04", "8719325500118")

    resultaat = dienst.geef_akkoord("G-PH.04", "Bas")

    assert resultaat.controle.kratten == 1
    assert resultaat.labels == 1


def test_akkoord_maakt_label_per_krat_en_pakbon_per_doos(dienst: PhService):
    dienst.start_controle("G-PH.01", "Bas")
    dienst.scan_barcode("G-PH.01", "8719325104871")
    dienst.scan_barcode("G-PH.01", "8719325104871")
    dienst.zet_krat("G-PH.01", 2)
    dienst.scan_barcode("G-PH.01", "8719325104888")
    dienst.scan_barcode("G-PH.01", "8719325104895")

    resultaat = dienst.geef_akkoord("G-PH.01", "Bas")

    assert resultaat.labels == 2
    assert len(resultaat.controle.tracking) == 2
    assert resultaat.controle.labels_geprint

    _ph, controle, kratten = dienst.pakbon_gegevens("G-PH.01")
    assert [k["nummer"] for k in kratten] == [1, 2]
    assert kratten[0]["regels"][0]["aantal"] == 2
    assert len(kratten[1]["regels"]) == 2
    assert controle.kratten == 2
    assert len(dienst.opslag.labels(controle.id)) == 2


def test_labelfout_blokkeert_de_afmelding_niet(dienst: PhService, srs, monkeypatch):
    from app.verzending import SendcloudError

    def stuk(*_a, **_k):
        raise SendcloudError("Sendcloud plat")

    monkeypatch.setattr(dienst.verzending, "maak_labels", stuk)

    dienst.start_controle("G-PH.04", "Bas")
    dienst.scan_barcode("G-PH.04", "8719325500118")
    resultaat = dienst.geef_akkoord("G-PH.04", "Bas")

    # De controle is akkoord en SRS weet ervan; alleen het label ontbreekt.
    assert resultaat.controle.is_akkoord
    assert srs.afmeldingen[0]["ph"] == "G-PH.04"
    assert "Sendcloud plat" in resultaat.label_fout
    onderzoek = dienst.opslag.open_onderzoek("G-PH.04")
    assert onderzoek is not None and "Label" in onderzoek.reden


def test_label_alsnog_maken_na_een_storing(dienst: PhService, monkeypatch):
    from app.verzending import SendcloudError

    def stuk(*_a, **_k):
        raise SendcloudError("Sendcloud plat")

    echt = dienst.verzending.maak_labels
    monkeypatch.setattr(dienst.verzending, "maak_labels", stuk)
    dienst.start_controle("G-PH.04", "Bas")
    dienst.scan_barcode("G-PH.04", "8719325500118")
    dienst.geef_akkoord("G-PH.04", "Bas")

    monkeypatch.setattr(dienst.verzending, "maak_labels", echt)
    assert dienst.maak_label_alsnog("G-PH.04", "Bas") == 1

    controle = dienst.opslag.laatste_controle("G-PH.04")
    assert controle.tracking and not controle.label_fout


def test_opnieuw_printen_maakt_geen_tweede_label(dienst: PhService):
    dienst.start_controle("G-PH.04", "Bas")
    dienst.scan_barcode("G-PH.04", "8719325500118")
    dienst.geef_akkoord("G-PH.04", "Bas")
    controle = dienst.opslag.laatste_controle("G-PH.04")

    assert dienst.print_label_opnieuw("G-PH.04", "Bas") == 1
    assert len(dienst.opslag.labels(controle.id)) == 1


def test_opnieuw_printen_zonder_levering(dienst: PhService):
    with pytest.raises(ControleFout):
        dienst.print_label_opnieuw("G-PH.01", "Bas")
