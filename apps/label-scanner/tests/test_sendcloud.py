import json

import httpx
import pytest
import respx

from app.config import Settings
from app.models import Order
from app.sendcloud import OrderNotFound, SendcloudClient, SendcloudError

BASE = "https://panel.sendcloud.sc/api"


def settings(**overrides) -> Settings:
    defaults = dict(
        sendcloud_public_key="pk",
        sendcloud_secret_key="sk",
        sendcloud_integration_id=42,
        sendcloud_api_base=BASE,
    )
    return Settings(**{**defaults, **overrides})


async def make_client(**overrides):
    return SendcloudClient(settings(**overrides), httpx.AsyncClient())


V3_ORDER = {
    "data": [
        {
            "id": "ord_1",
            "order_number": "#1042",
            "shipping_address": {
                "name": "Jan Jansen",
                "address_line_1": "Voorbeeldstraat",
                "house_number": "12",
                "postal_code": "5611 AA",
                "city": "Eindhoven",
                "country": {"iso_2": "NL", "name": "Netherlands"},
            },
            "shipping_option": {"id": 1675, "name": "PostNL Standard 0-23kg"},
            "weight": {"value": "2.500"},
            "parcels": [],
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_find_order_uses_v3_and_ignores_hash_prefix():
    respx.get(f"{BASE}/v3/orders").mock(return_value=httpx.Response(200, json=V3_ORDER))
    client = await make_client()

    order = await client.find_order("1042")

    assert order.id == "ord_1"
    assert order.address == "Voorbeeldstraat 12"
    assert order.current_shipping_method_id == 1675
    assert order.weight_kg == 2.5
    assert order.already_announced is False


@pytest.mark.asyncio
@respx.mock
async def test_find_order_falls_back_to_v2_when_v3_missing():
    respx.get(f"{BASE}/v3/orders").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/v2/parcels").mock(
        return_value=httpx.Response(
            200,
            json={
                "parcels": [
                    {
                        "id": 99,
                        "order_number": "1042",
                        "name": "Jan Jansen",
                        "address": "Voorbeeldstraat 12",
                        "postal_code": "5611 AA",
                        "city": "Eindhoven",
                        "country": {"iso_2": "NL", "name": "Netherlands"},
                        "shipment": {"id": 8, "name": "Unstamped letter"},
                        "weight": "1.000",
                        "status": {"message": "Ready to send"},
                    }
                ]
            },
        )
    )
    client = await make_client()

    order = await client.find_order("1042")

    assert order.id == "99"
    assert order.current_shipping_method_name == "Unstamped letter"


@pytest.mark.asyncio
@respx.mock
async def test_find_order_raises_when_nothing_matches():
    respx.get(f"{BASE}/v3/orders").mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(f"{BASE}/v2/parcels").mock(return_value=httpx.Response(200, json={"parcels": []}))
    client = await make_client()

    with pytest.raises(OrderNotFound):
        await client.find_order("1042")


@pytest.mark.asyncio
@respx.mock
async def test_shipping_methods_put_current_method_first():
    respx.get(f"{BASE}/v2/shipping_methods").mock(
        return_value=httpx.Response(
            200,
            json={
                "shipping_methods": [
                    {"id": 8, "name": "Unstamped letter", "carrier": "postnl",
                     "min_weight": "0.001", "max_weight": "2.000"},
                    {"id": 1675, "name": "PostNL Standard", "carrier": "postnl",
                     "min_weight": "0.001", "max_weight": "23.000"},
                ]
            },
        )
    )
    client = await make_client()
    order = Order(id="1", order_number="1042", country_code="NL", current_shipping_method_id=1675)

    methods = await client.shipping_methods(order)

    assert methods[0].id == 1675
    assert methods[0].is_current is True
    assert methods[0].max_weight_kg == 23.0


@pytest.mark.asyncio
@respx.mock
async def test_single_label_uses_sync_endpoint():
    route = respx.post(f"{BASE}/v3/orders/create-label-sync").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "parcels": [
                        {
                            "parcel_id": "p1",
                            "tracking_number": "3SABC123",
                            "label": {"file": "SkVMTE8=", "mime_type": "application/pdf"},
                        }
                    ]
                }
            },
        )
    )
    client = await make_client()
    order = Order(id="ord_1", order_number="1042")

    labels = await client.create_labels(order, shipping_method_id=1675, quantity=1)

    assert [label.parcel_id for label in labels] == ["p1"]
    body = json.loads(route.calls.last.request.content)
    assert body["order"]["order_id"] == "ord_1"
    assert body["order"]["shipping_option"]["id"] == 1675
    assert body["label"]["mime_type"] == "application/pdf"


@pytest.mark.asyncio
@respx.mock
async def test_multicollo_uses_async_endpoint_and_polls():
    respx.post(f"{BASE}/v3/orders/create-labels-async").mock(
        return_value=httpx.Response(202, json={"data": {"id": "job_1", "status": "processing"}})
    )
    respx.get(f"{BASE}/v3/orders/create-labels-async/job_1").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"status": "processing"}}),
            httpx.Response(
                200,
                json={
                    "data": {
                        "status": "completed",
                        "parcels": [
                            {"parcel_id": "p1", "tracking_number": "3S1",
                             "label": {"file": "SkVMTE8=", "mime_type": "application/pdf"}},
                            {"parcel_id": "p2", "tracking_number": "3S2",
                             "label": {"file": "SkVMTE8=", "mime_type": "application/pdf"}},
                        ],
                    }
                },
            ),
        ]
    )
    client = await make_client()
    order = Order(id="ord_1", order_number="1042")

    labels = await client.create_labels(order, shipping_method_id=1675, quantity=2)

    assert [label.tracking_number for label in labels] == ["3S1", "3S2"]


@pytest.mark.asyncio
@respx.mock
async def test_api_errors_are_surfaced_readably():
    respx.post(f"{BASE}/v3/orders/create-label-sync").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Weight exceeds the shipping method maximum"}}
        )
    )
    client = await make_client()

    with pytest.raises(SendcloudError, match="Weight exceeds"):
        await client.create_labels(Order(id="1", order_number="1"), 1675, 1)
