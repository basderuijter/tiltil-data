"""Tests voor de REST-koppeling met een nagebootste SRS-webservice."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Instellingen
from app.models import Controle, ControleRegel
from app.srs.base import PhNietGevonden, SrsFout
from app.srs.rest import RestSrsClient, haal_pad

MAPPING = json.loads(
    (__import__("pathlib").Path(__file__).resolve().parent.parent / "config" / "srs_rest.json")
    .read_text(encoding="utf-8")
)

PH_JSON = {
    "locationCode": "G-PH.01",
    "orderReference": "WEB-104872",
    "orderId": 104872,
    "orderDate": "07-08-2026",
    "status": "open",
    "shippingMethod": "PostNL standaard",
    "remark": "",
    "shipping": {
        "name": "Marieke de Groot",
        "street": "Zandvoortselaan 112",
        "postalCity": "2106 AR Heemstede",
        "country": "Nederland",
    },
    "lines": [
        {
            "barcode": "8719325104871",
            "description": "Vaas geribbeld zand M",
            "quantityOrdered": 2,
            "quantityInLocation": "2",
            "articleCode": "HOME-VAAS-M",
            "pickLocation": "K.A.12",
        }
    ],
}


def maak_client(handler) -> RestSrsClient:
    instellingen = Instellingen(srs_backend="rest", srs_base_url="https://srs.test")
    client = RestSrsClient(instellingen, mapping=MAPPING)
    client._client = httpx.Client(
        base_url="https://srs.test", transport=httpx.MockTransport(handler)
    )
    return client


def test_haal_pad_leest_geneste_waarden():
    assert haal_pad(PH_JSON, "shipping.name") == "Marieke de Groot"
    assert haal_pad(PH_JSON, "lines[0].barcode") == "8719325104871"
    assert haal_pad(PH_JSON, "shipping.onbekend", "leeg") == "leeg"
    assert haal_pad(PH_JSON, "lines[9].barcode", "geen") == "geen"


def test_lijst_en_detail_worden_gemapt():
    def handler(verzoek: httpx.Request) -> httpx.Response:
        if verzoek.url.path == "/api/v1/picklocations":
            assert verzoek.url.params["type"] == "PH"
            return httpx.Response(200, json={"data": [PH_JSON]})
        return httpx.Response(200, json={"data": PH_JSON})

    client = maak_client(handler)

    phs = client.lijst_phs()
    assert [p.code for p in phs] == ["G-PH.01"]

    ph = client.haal_ph("G-PH.01")
    assert ph.klant == "Marieke de Groot"
    assert ph.regels[0].aantal_in_ph == 2  # string uit SRS wordt netjes een int
    assert ph.regels[0].picklocatie == "K.A.12"


def test_afmelding_stuurt_getelde_aantallen():
    verstuurd: dict = {}

    def handler(verzoek: httpx.Request) -> httpx.Response:
        if verzoek.method == "POST":
            verstuurd.update(json.loads(verzoek.content))
            return httpx.Response(200, json={"data": {"reference": "SRS-8811"}})
        return httpx.Response(200, json={"data": PH_JSON})

    client = maak_client(handler)
    ph = client.haal_ph("G-PH.01")
    controle = Controle(
        ph_code=ph.code,
        order_referentie=ph.order_referentie,
        akkoord_door="Bas",
        regels=[
            ControleRegel(
                barcode="8719325104871",
                omschrijving="Vaas",
                aantal_verwacht=2,
                aantal_geteld=2,
            )
        ],
    )

    referentie = client.meld_ph_gereed(ph, controle)

    assert referentie == "SRS-8811"
    assert verstuurd["locationCode"] == "G-PH.01"
    assert verstuurd["checkedBy"] == "Bas"
    assert verstuurd["status"] == "checked"
    assert verstuurd["lines"] == [{"barcode": "8719325104871", "quantity": 2}]


def test_404_wordt_ph_niet_gevonden():
    client = maak_client(lambda _v: httpx.Response(404, json={"message": "not found"}))
    with pytest.raises(PhNietGevonden):
        client.haal_ph("G-PH.99")


def test_serverfout_wordt_srsfout():
    client = maak_client(lambda _v: httpx.Response(500, text="boem"))
    with pytest.raises(SrsFout):
        client.lijst_phs()


def test_netwerkfout_wordt_srsfout():
    def handler(_verzoek: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("geen verbinding")

    client = maak_client(handler)
    with pytest.raises(SrsFout):
        client.lijst_phs()


def test_base_url_verplicht():
    with pytest.raises(SrsFout):
        RestSrsClient(Instellingen(srs_backend="rest", srs_base_url=""), mapping=MAPPING)
