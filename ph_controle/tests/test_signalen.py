from __future__ import annotations

from datetime import datetime, timedelta

from app.completeness import ph_status
from app.models import Controle, Observatie, Onderzoek, Ph, PhRegel
from app.signalen import (
    AFGEMELD,
    BESCHIKBAAR,
    IN_CONTROLE,
    KLAAR,
    LET_OP,
    NORMAAL,
    VASTLOPER,
    VULLEND,
    Drempels,
    beoordeel,
    beschrijf_duur,
    haltoestand,
    vingerafdruk,
)

NU = datetime(2026, 8, 10, 15, 0)
DREMPELS = Drempels(letop_uren=4, vastloper_uren=24, stilstand_uren=8)


def maak_ph(*paren: tuple[int, int]) -> Ph:
    return Ph(
        code="G-PH.01",
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


def observatie(uren_oud: float, stil_uren: float, compleet_uren: float | None = None):
    return Observatie(
        ph_code="G-PH.01",
        eerst_gezien=NU - timedelta(hours=uren_oud),
        laatst_gewijzigd=NU - timedelta(hours=stil_uren),
        compleet_sinds=NU - timedelta(hours=compleet_uren) if compleet_uren is not None else None,
    )


def beoordeel_ph(ph, obs, **kwargs):
    return beoordeel(ph, ph_status(ph), obs, NU, DREMPELS, **kwargs)


def test_verse_incomplete_ph_loopt_gewoon():
    signaal = beoordeel_ph(maak_ph((2, 1)), observatie(1, 0.5))
    assert signaal.niveau == NORMAAL
    assert not signaal.vraagt_aandacht


def test_incompleet_na_letop_drempel_vraagt_aandacht():
    signaal = beoordeel_ph(maak_ph((2, 1)), observatie(5, 1))
    assert signaal.niveau == LET_OP
    assert "5 uur" in signaal.redenen[0]


def test_te_lang_incompleet_is_vastloper():
    signaal = beoordeel_ph(maak_ph((2, 1)), observatie(30, 1))
    assert signaal.niveau == VASTLOPER
    assert any("Ontbreekt" in reden for reden in signaal.redenen)


def test_geen_voortgang_is_vastloper():
    signaal = beoordeel_ph(maak_ph((2, 1)), observatie(9, 9))
    assert signaal.niveau == VASTLOPER
    assert "Geen voortgang" in signaal.redenen[0]


def test_complete_ph_die_blijft_liggen():
    ph = maak_ph((1, 1))
    assert beoordeel_ph(ph, observatie(5, 5, compleet_uren=1)).niveau == NORMAAL
    assert beoordeel_ph(ph, observatie(9, 9, compleet_uren=5)).niveau == LET_OP
    assert beoordeel_ph(ph, observatie(30, 30, compleet_uren=26)).niveau == VASTLOPER


def test_teveel_in_ph_vraagt_altijd_aandacht():
    assert beoordeel_ph(maak_ph((1, 3)), observatie(0.5, 0.5)).niveau == LET_OP
    assert beoordeel_ph(maak_ph((1, 3)), observatie(6, 6)).niveau == VASTLOPER


def test_open_onderzoek_maakt_vastloper():
    onderzoek = Onderzoek(ph_code="G-PH.01", reden="Artikel zoek", geopend_op=NU)
    signaal = beoordeel_ph(maak_ph((1, 1)), observatie(1, 1, 1), onderzoek=onderzoek)
    assert signaal.niveau == VASTLOPER
    assert "Artikel zoek" in signaal.redenen[0]


def test_afgemelde_controle_geeft_geen_signaal():
    controle = Controle(ph_code="G-PH.01", order_referentie="WEB-1", akkoord_op=NU)
    signaal = beoordeel_ph(maak_ph((2, 1)), observatie(40, 40), controle=controle)
    assert signaal.niveau == NORMAAL


def test_gemelde_afwijking_maakt_vastloper():
    controle = Controle(
        ph_code="G-PH.01", order_referentie="WEB-1", afwijking_notitie="Vaas gebroken"
    )
    signaal = beoordeel_ph(maak_ph((1, 1)), observatie(1, 1, 1), controle=controle)
    assert signaal.niveau == VASTLOPER
    assert "Vaas gebroken" in signaal.redenen[0]


def test_vingerafdruk_verandert_alleen_bij_inhoudswijziging():
    assert vingerafdruk(maak_ph((2, 1))) == vingerafdruk(maak_ph((2, 1)))
    assert vingerafdruk(maak_ph((2, 1))) != vingerafdruk(maak_ph((2, 2)))


def test_beschrijf_duur():
    assert beschrijf_duur(0.5) == "30 min"
    assert beschrijf_duur(6) == "6 uur"
    assert beschrijf_duur(48) == "2 dagen"


def test_haltoestanden():
    ph = maak_ph((2, 1))
    status = ph_status(ph)
    compleet = ph_status(maak_ph((2, 2)))
    normaal = beoordeel_ph(ph, observatie(1, 1))
    vast = beoordeel_ph(ph, observatie(30, 30))

    assert haltoestand(None, None, None) == BESCHIKBAAR
    assert haltoestand(status, normaal, None) == VULLEND
    assert haltoestand(compleet, normaal, None) == KLAAR
    assert haltoestand(compleet, normaal, Controle("G-PH.01", "WEB-1")) == IN_CONTROLE
    assert haltoestand(compleet, normaal, Controle("G-PH.01", "WEB-1", akkoord_op=NU)) == AFGEMELD
    assert haltoestand(status, vast, None) == VASTLOPER
