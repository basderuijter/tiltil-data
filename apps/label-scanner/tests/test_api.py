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
    current = [m for m in payload["shipping_methods"] if m["is_current"]]
    assert len(current) == 1
    assert current[0]["id"] == payload["order"]["current_shipping_method_id"]
    # The method already on the order is offered first, but every other method
    # stays selectable.
    assert payload["shipping_methods"][0]["is_current"] is True
    assert len(payload["shipping_methods"]) > 1


def test_creating_labels_returns_one_label_per_parcel(client, tmp_path):
    response = client.post(
        "/api/labels",
        json={"order_number": "1042", "shipping_method_id": 2100, "quantity": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["labels"]) == 3
    assert payload["printed"] is True
    assert payload["shipping_method_name"] == "DHL Parcel Connect 0-31.5kg"
    assert len(list(tmp_path.glob("*.pdf"))) == 3


def test_quantity_must_be_at_least_one(client):
    response = client.post(
        "/api/labels",
        json={"order_number": "1042", "shipping_method_id": 8, "quantity": 0},
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
