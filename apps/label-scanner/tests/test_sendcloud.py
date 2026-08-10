import json

import httpx
import pytest
import respx

from app.config import Settings
from app.models import Order, OrderItem
from app.sendcloud import (
    OrderNotFound,
    SendcloudClient,
    SendcloudError,
    split_items,
)

BASE = "https://panel.sendcloud.sc/api"


def settings(**overrides) -> Settings:
    defaults = dict(
        sendcloud_public_key="pk",
        sendcloud_secret_key="sk",
        sendcloud_integration_id=70,
        sendcloud_api_base=BASE,
    )
    return Settings(**{**defaults, **overrides})


async def make_client(**overrides):
    return SendcloudClient(settings(**overrides), httpx.AsyncClient())


V3_ORDER = {
    "id": "669",
    "order_number": "#1042",
    "shipping_address": {
        "name": "Jan Jansen",
        "address_line_1": "Stadhuisplein",
        "house_number": "15",
        "postal_code": "5341TW",
        "city": "Oss",
        "country_code": "NL",
    },
    "shipping_details": {
        "delivery_indicator": "PostNL Standard",
        "measurement": {"weight": {"value": 2.5, "unit": "kg"}},
        "ship_with": {
            "type": "shipping_option_code",
            "properties": {"shipping_option_code": "postnl:standard"},
        },
    },
    "order_details": {
        "order_items": [
            {"item_id": "5552", "quantity": 2, "name": "Cylinder candle"},
            {"item_id": "5555", "quantity": 1, "name": "Linnen kussen"},
        ]
    },
}


@pytest.mark.asyncio
@respx.mock
async def test_find_order_reads_v3_shape_and_ignores_hash_prefix():
    respx.get(f"{BASE}/v3/orders").mock(
        return_value=httpx.Response(200, json={"data": [V3_ORDER]})
    )
    client = await make_client()

    order = await client.find_order("1042")

    assert order.id == "669"
    assert order.address == "Stadhuisplein 15"
    assert order.current_shipping_option_code == "postnl:standard"
    assert order.weight_kg == 2.5
    assert order.total_units == 3


@pytest.mark.asyncio
@respx.mock
async def test_find_order_falls_back_to_v2_when_v3_search_unavailable():
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
                        "address": "Stadhuisplein 15",
                        "postal_code": "5341TW",
                        "city": "Oss",
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
    assert order.current_shipping_option_name == "Unstamped letter"
    # v2 knows no v3 option code, so nothing gets preselected.
    assert order.current_shipping_option_code == ""


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
async def test_single_label_posts_documented_sync_body_and_returns_label():
    route = respx.post(f"{BASE}/v3/orders/create-label-sync").mock(
        return_value=httpx.Response(
            201,
            json={
                "data": {
                    "parcel_id": 420,
                    "shipment_id": "511",
                    "tracking_number": "3SABC123",
                    "label": {"file": "SkVMTE8=", "mime_type": "application/pdf", "dpi": 72},
                }
            },
        )
    )
    client = await make_client()

    labels = await client.create_labels(
        Order(id="669", order_number="1042"), "postnl:standard", 1
    )

    assert [label.tracking_number for label in labels] == ["3SABC123"]
    body = json.loads(route.calls.last.request.content)
    assert body["integration_id"] == 70
    assert body["order"]["order_number"] == "1042"
    assert body["ship_with"] == {
        "type": "shipping_option_code",
        "properties": {"shipping_option_code": "postnl:standard", "contract_id": None},
    }
    assert body["label_details"] == {"mime_type": "application/pdf", "dpi": 72}


@pytest.mark.asyncio
@respx.mock
async def test_sync_label_also_accepts_the_label_details_shape():
    respx.post(f"{BASE}/v3/orders/create-label-sync").mock(
        return_value=httpx.Response(
            201,
            json={"data": [{"parcel_id": 420, "label_details": {"file": "SkVMTE8="}}]},
        )
    )
    client = await make_client()

    labels = await client.create_labels(Order(id="1", order_number="1"), "postnl:standard", 1)

    assert labels[0].file_base64 == "SkVMTE8="


@pytest.mark.asyncio
@respx.mock
async def test_multicollo_announces_async_then_downloads_each_label():
    async_route = respx.post(f"{BASE}/v3/orders/create-labels-async").mock(
        return_value=httpx.Response(
            202,
            json={
                "data": [
                    {"parcel_id": 420, "parcels_ids": [420, 421], "shipment_id": "511"}
                ]
            },
        )
    )
    respx.get(f"{BASE}/v3/shipments/511").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"parcels": [{"id": 420}, {"id": 421}]}}),
            httpx.Response(
                200,
                json={
                    "data": {
                        "parcels": [
                            {
                                "id": 420,
                                "tracking_number": "3S1",
                                "documents": [
                                    {
                                        "document_type": "label",
                                        "size": "a6",
                                        "link": f"{BASE}/v3/parcels/420/documents/label",
                                    }
                                ],
                            },
                            {
                                "id": 421,
                                "tracking_number": "3S2",
                                "documents": [
                                    {
                                        "document_type": "label",
                                        "size": "a6",
                                        "link": f"{BASE}/v3/parcels/421/documents/label",
                                    }
                                ],
                            },
                        ]
                    }
                },
            ),
        ]
    )
    respx.get(f"{BASE}/v3/parcels/420/documents/label").mock(
        return_value=httpx.Response(200, content=b"PDF-420")
    )
    respx.get(f"{BASE}/v3/parcels/421/documents/label").mock(
        return_value=httpx.Response(200, content=b"PDF-421")
    )
    client = await make_client()
    order = Order(
        id="669",
        order_number="1042",
        items=[OrderItem(item_id="5552", quantity=2), OrderItem(item_id="5555", quantity=1)],
    )

    labels = await client.create_labels(order, "postnl:standard", 2)

    assert [label.tracking_number for label in labels] == ["3S1", "3S2"]
    assert [label.file_base64 for label in labels] == ["UERGLTQyMA==", "UERGLTQyMQ=="]
    body = json.loads(async_route.calls.last.request.content)
    assert len(body["orders"][0]["parcels"]) == 2


@pytest.mark.asyncio
@respx.mock
async def test_multicollo_fetches_items_when_the_lookup_did_not_carry_them():
    respx.get(f"{BASE}/v3/orders/669").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"order_details": {"order_items": [{"item_id": "1", "quantity": 4}]}}},
        )
    )
    route = respx.post(f"{BASE}/v3/orders/create-labels-async").mock(
        return_value=httpx.Response(202, json={"data": [{"shipment_id": "511"}]})
    )
    respx.get(f"{BASE}/v3/shipments/511").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "parcels": [
                        {
                            "id": n,
                            "documents": [
                                {"document_type": "label", "link": f"{BASE}/v3/parcels/{n}/documents/label"}
                            ],
                        }
                        for n in (1, 2)
                    ]
                }
            },
        )
    )
    respx.get(url__regex=rf"{BASE}/v3/parcels/\d+/documents/label").mock(
        return_value=httpx.Response(200, content=b"PDF")
    )
    client = await make_client()

    await client.create_labels(Order(id="669", order_number="1042"), "postnl:standard", 2)

    parcels = json.loads(route.calls.last.request.content)["orders"][0]["parcels"]
    assert [p["parcel_items"] for p in parcels] == [
        [{"item_id": "1", "quantity": 2}],
        [{"item_id": "1", "quantity": 2}],
    ]


@pytest.mark.asyncio
@respx.mock
async def test_partial_failures_from_the_async_endpoint_are_raised():
    respx.post(f"{BASE}/v3/orders/create-labels-async").mock(
        return_value=httpx.Response(
            202,
            json={
                "data": [],
                "errors": [
                    {
                        "status": "400",
                        "code": "validation_error",
                        "detail": "The order has no valid shipping address.",
                    }
                ],
            },
        )
    )
    client = await make_client()
    order = Order(id="1", order_number="1", items=[OrderItem(item_id="1", quantity=5)])

    with pytest.raises(SendcloudError, match="no valid shipping address"):
        await client.create_labels(order, "postnl:standard", 2)


@pytest.mark.asyncio
@respx.mock
async def test_api_errors_are_surfaced_readably():
    respx.post(f"{BASE}/v3/orders/create-label-sync").mock(
        return_value=httpx.Response(
            400,
            json={
                "errors": [
                    {"status": "400", "code": "invalid", "detail": "Input should be a valid string."}
                ]
            },
        )
    )
    client = await make_client()

    with pytest.raises(SendcloudError, match="valid string"):
        await client.create_labels(Order(id="1", order_number="1"), "postnl:standard", 1)


@pytest.mark.asyncio
@respx.mock
async def test_shipping_options_fall_back_to_the_configured_file(tmp_path):
    respx.post(f"{BASE}/v3/shipping-options").mock(return_value=httpx.Response(404))
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps(
            [
                {"code": "postnl:letterbox", "name": "Brievenbuspakket", "carrier": "postnl"},
                {"code": "postnl:standard", "name": "PostNL Standard", "carrier": "postnl"},
            ]
        )
    )
    client = await make_client(shipping_options_file=path)
    order = Order(id="1", order_number="1", current_shipping_option_code="postnl:standard")

    options = await client.shipping_options(order)

    assert options[0].code == "postnl:standard"
    assert options[0].is_current is True
    assert len(options) == 2


# --- multicollo splitting ----------------------------------------------------


def test_split_items_distributes_units_over_the_boxes():
    items = [OrderItem(item_id="a", quantity=3), OrderItem(item_id="b", quantity=1)]

    parcels = split_items(items, 2)

    assert len(parcels) == 2
    total = sum(i["quantity"] for p in parcels for i in p["parcel_items"])
    assert total == 4


def test_split_items_refuses_more_boxes_than_items():
    with pytest.raises(SendcloudError, match="1 artikel"):
        split_items([OrderItem(item_id="a", quantity=1)], 3)


def test_split_items_explains_when_the_order_has_no_item_lines():
    with pytest.raises(SendcloudError, match="geen artikelregels"):
        split_items([], 2)
