from __future__ import annotations

from app.completeness import (
    COMPLEET,
    INCOMPLEET,
    LEEG,
    TEVEEL,
    controle_regels_uit_ph,
    mag_akkoord,
    ph_status,
    scan,
)
from app.models import Controle, Ph, PhRegel


def maak_ph(*paren: tuple[int, int]) -> Ph:
    return Ph(
        code="G-PH.99",
        order_referentie="WEB-1",
        regels=tuple(
            PhRegel(
                barcode=f"BC{i}",
                omschrijving=f"Artikel {i}",
                aantal_besteld=besteld,
                aantal_in_ph=aanwezig,
            )
            for i, (besteld, aanwezig) in enumerate(paren, start=1)
        ),
    )


def test_volledig_gevulde_ph_is_compleet():
    status = ph_status(maak_ph((2, 2), (1, 1)))
    assert status.status == COMPLEET
    assert status.is_compleet and status.mag_uit_ph
    assert status.aantal_besteld == 3 and status.aantal_in_ph == 3


def test_ontbrekende_stuks_maken_ph_incompleet():
    status = ph_status(maak_ph((2, 1), (1, 1)))
    assert status.status == INCOMPLEET
    assert not status.mag_uit_ph
    assert status.aantal_ontbrekend == 1
    assert [r.barcode for r in status.ontbrekend] == ["BC1"]


def test_lege_ph():
    assert ph_status(maak_ph((2, 0), (1, 0))).status == LEEG


def test_teveel_in_ph_blokkeert_leeghalen():
    status = ph_status(maak_ph((1, 3)))
    assert status.status == TEVEEL
    assert status.is_compleet
    assert not status.mag_uit_ph
    assert [r.teveel for r in status.overtollig] == [2]


def maak_controle(ph: Ph) -> Controle:
    return Controle(
        ph_code=ph.code, order_referentie=ph.order_referentie, regels=controle_regels_uit_ph(ph)
    )


def test_scan_telt_op_en_stopt_bij_besteld_aantal():
    controle = maak_controle(maak_ph((2, 2)))

    assert scan(controle, "BC1").status == "geteld"
    assert scan(controle, "BC1").status == "geteld"
    derde = scan(controle, "BC1")

    assert derde.status == "te_veel"
    assert controle.regel("BC1").aantal_geteld == 2


def test_onbekende_barcode_wordt_geweigerd():
    controle = maak_controle(maak_ph((1, 1)))
    resultaat = scan(controle, "XXX")
    assert resultaat.status == "onbekend"
    assert controle.totaal_geteld == 0


def test_akkoord_pas_als_alles_geteld_en_in_goede_staat():
    controle = maak_controle(maak_ph((2, 2), (1, 1)))
    assert not mag_akkoord(controle).mag_akkoord

    scan(controle, "BC1")
    scan(controle, "BC1")
    scan(controle, "BC2")
    assert mag_akkoord(controle).mag_akkoord

    controle.regel("BC2").conditie = "beschadigd"
    check = mag_akkoord(controle)
    assert not check.mag_akkoord
    assert any("beschadigd" in reden for reden in check.redenen)


def test_lege_controle_kan_niet_akkoord():
    controle = Controle(ph_code="G-PH.0", order_referentie="WEB-0")
    assert not mag_akkoord(controle).mag_akkoord
