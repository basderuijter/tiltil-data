import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def client(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(
        demo_mode=True, print_backend="none", spool_dir=tmp_path, max_parcels=4
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_scan_returns_order_with_current_method_marked(client):
    response = client.get("/api/orders/1042")

    assert response.status_code == 200
    payload = response.json()
    assert payload["order"]["order_number"] == "1042"
    assert payload["max_parcels"] == 4
    current = [m for m in payload["shipping_options"] if m["is_current"]]
    assert len(current) == 1
    assert current[0]["code"] == payload["order"]["current_shipping_option_code"]
    # The method already on the order is offered first, but every other method
    # stays selectable.
    assert payload["shipping_options"][0]["is_current"] is True
    assert len(payload["shipping_options"]) > 1


def test_creating_labels_returns_one_label_per_parcel(client, tmp_path):
    response = client.post(
        "/api/labels",
        json={"order_number": "1042", "shipping_option_code": "dhl:parcel_connect", "quantity": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["labels"]) == 3
    assert payload["printed"] is True
    assert payload["shipping_option_name"] == "DHL Parcel Connect"
    assert len(list(tmp_path.glob("*.pdf"))) == 3


def test_quantity_must_be_at_least_one(client):
    response = client.post(
        "/api/labels",
        json={"order_number": "1042", "shipping_option_code": "postnl:standard", "quantity": 0},
    )

    assert response.status_code == 422


def test_health_reports_configuration(client):
    payload = client.get("/api/health").json()

    assert payload["ok"] is True
    assert payload["demo_mode"] is True


def test_live_mode_without_keys_fails_loudly(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(
        demo_mode=False, print_backend="none", spool_dir=tmp_path
    )
    with TestClient(app) as test_client:
        response = test_client.get("/api/orders/1042")
    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "sleutels" in response.json()["detail"]


def test_stations_are_listed_when_configured(tmp_path):
    stations = tmp_path / "stations.json"
    stations.write_text(
        '[{"id": "tafel-01", "name": "Tafel 1", "cups_host": "t1.local",'
        ' "cups_printer": "zd220"}]'
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        demo_mode=True, print_backend="none", spool_dir=tmp_path, stations_file=stations
    )
    with TestClient(app) as test_client:
        listed = test_client.get("/api/stations").json()
        # Without a station the app cannot know which printer to use, so the
        # label is still made but the print is reported as failed.
        result = test_client.post(
            "/api/labels",
            json={
                "order_number": "1042",
                "shipping_option_code": "postnl:standard",
                "quantity": 1,
            },
        ).json()
    app.dependency_overrides.clear()

    assert [s["id"] for s in listed] == ["tafel-01"]
    assert len(result["labels"]) == 1
    assert result["printed"] is False
    assert "tafel" in result["print_error"].lower()


def test_single_table_setup_needs_no_station(client):
    assert client.get("/api/stations").json() == []


def test_label_is_reported_to_the_owning_system(tmp_path, respx_mock):
    route = respx_mock.post("https://orders.internal/labels").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        demo_mode=True,
        print_backend="none",
        spool_dir=tmp_path,
        callback_url="https://orders.internal/labels",
        callback_token="s3cret",
    )
    with TestClient(app) as test_client:
        result = test_client.post(
            "/api/labels",
            json={
                "order_number": "1042-1",
                "shipping_option_code": "postnl:standard",
                "quantity": 2,
                "station": None,
            },
        ).json()
    app.dependency_overrides.clear()

    assert result["reported"] is True
    sent = json.loads(route.calls.last.request.content)
    assert sent["order_number"] == "1042-1"
    # Shopify needs the carrier to build a clickable track & trace link.
    assert sent["carrier"] == "postnl"
    assert sent["shipping_option_code"] == "postnl:standard"
    assert [p["tracking_number"] for p in sent["parcels"]] == [
        "3SDEMO00000001",
        "3SDEMO00000002",
    ]
    assert route.calls.last.request.headers["authorization"] == "Bearer s3cret"


def test_a_failed_report_never_blocks_the_label(tmp_path, respx_mock):
    respx_mock.post("https://orders.internal/labels").mock(
        return_value=httpx.Response(500)
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        demo_mode=True,
        print_backend="none",
        spool_dir=tmp_path,
        callback_url="https://orders.internal/labels",
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/labels",
            json={
                "order_number": "1042-1",
                "shipping_option_code": "postnl:standard",
                "quantity": 1,
            },
        )
    app.dependency_overrides.clear()

    # The parcel is real and the packer cannot fix an integration, so the label
    # still succeeds — but the failure is visible for monitoring.
    assert response.status_code == 200
    assert response.json()["printed"] is True
    assert response.json()["reported"] is False


def test_an_extra_label_can_be_added_after_the_first_one(client, tmp_path):
    # The packer finds mid-pack that it does not fit in one box.
    first = client.post(
        "/api/labels",
        json={"order_number": "1042", "shipping_option_code": "postnl:standard", "quantity": 1},
    ).json()
    extra = client.post(
        "/api/labels/extra",
        json={"order_number": "1042", "shipping_option_code": "postnl:standard"},
    )

    assert extra.status_code == 200
    assert len(first["labels"]) == 1
    assert len(extra.json()["labels"]) == 1
    assert extra.json()["printed"] is True


def test_scanning_a_delivery_with_a_method_skips_the_confirmation(client):
    payload = client.get("/api/orders/1042").json()

    # Nothing left to confirm, so the UI makes the label straight away.
    assert payload["fast_mode"] is True
