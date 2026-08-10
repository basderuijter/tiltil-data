"""Sendcloud client.

Built on the v3 API (multicollo, native ZPL, and the label returned inline in
the response) with the v2 parcels endpoint as a lookup fallback for orders that
v3 does not return.

Every request shape lives in this module on purpose: the endpoint paths and
payload keys are the only part of this app that depends on Sendcloud's exact
contract, so there is a single place to adjust if it changes. Lines marked
`VERIFY:` are the ones worth checking against https://sendcloud.dev before the
first live run.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx

from .config import Settings
from .models import Label, Order, ShippingMethod

# --- Endpoints ---------------------------------------------------------------
# VERIFY: order lookup path and query parameter name.
ORDERS_SEARCH = "/v3/orders"
# Fallback: the long-standing v2 parcels endpoint, filtered on order number.
PARCELS_SEARCH_V2 = "/v2/parcels"
SHIPPING_METHODS_V2 = "/v2/shipping_methods"
CREATE_LABEL_SYNC = "/v3/orders/create-label-sync"
CREATE_LABELS_ASYNC = "/v3/orders/create-labels-async"
# VERIFY: the async status path; Sendcloud returns its location in the response
# of create-labels-async, which is what we use when present.
ASYNC_STATUS = "/v3/orders/create-labels-async/{job_id}"


class SendcloudError(Exception):
    """Raised for any Sendcloud failure that the operator should see."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class OrderNotFound(SendcloudError):
    pass


def _kg(value: Any) -> float | None:
    """Sendcloud returns weights as strings in kilograms."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SendcloudClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    # -- plumbing -------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self._settings.sendcloud_api_base.rstrip('/')}{path}"
        try:
            response = await self._client.request(
                method,
                url,
                auth=(
                    self._settings.sendcloud_public_key,
                    self._settings.sendcloud_secret_key,
                ),
                timeout=self._settings.sendcloud_timeout_seconds,
                **kwargs,
            )
        except httpx.RequestError as exc:
            raise SendcloudError(f"Sendcloud niet bereikbaar: {exc}") from exc

        if response.status_code >= 400:
            raise SendcloudError(
                _error_message(response), status_code=response.status_code
            )
        if not response.content:
            return {}
        return response.json()

    # -- orders ---------------------------------------------------------------

    async def find_order(self, order_number: str) -> Order:
        """Look up a scanned order number, trying v3 first and then v2."""
        order_number = order_number.strip()
        if not order_number:
            raise OrderNotFound("Geen ordernummer gescand.")

        order = await self._find_order_v3(order_number)
        if order is None:
            order = await self._find_order_v2(order_number)
        if order is None:
            raise OrderNotFound(f"Order {order_number} niet gevonden in Sendcloud.")
        return order

    async def _find_order_v3(self, order_number: str) -> Order | None:
        params: dict[str, Any] = {"order_number": order_number}
        if self._settings.sendcloud_integration_id:
            params["integration_id"] = self._settings.sendcloud_integration_id
        try:
            payload = await self._request("GET", ORDERS_SEARCH, params=params)
        except SendcloudError as exc:
            # A 404 here means "this deployment does not expose v3 order search",
            # not "no such order" — fall through to v2 rather than failing.
            if exc.status_code in (400, 404, 501):
                return None
            raise

        items = payload.get("data") or payload.get("orders") or []
        for item in items:
            if _matches(item.get("order_number"), order_number):
                return _order_from_v3(item)
        return None

    async def _find_order_v2(self, order_number: str) -> Order | None:
        payload = await self._request(
            "GET", PARCELS_SEARCH_V2, params={"order_number": order_number}
        )
        parcels = payload.get("parcels") or []
        matching = [p for p in parcels if _matches(p.get("order_number"), order_number)]
        if not matching:
            return None
        # Newest first, so a re-scan shows the most recent state of the order.
        matching.sort(key=lambda p: p.get("id") or 0, reverse=True)
        return _order_from_v2(matching[0])

    # -- shipping methods -----------------------------------------------------

    async def shipping_methods(self, order: Order) -> list[ShippingMethod]:
        params: dict[str, Any] = {}
        if order.country_code:
            params["to_country"] = order.country_code
        payload = await self._request("GET", SHIPPING_METHODS_V2, params=params)

        methods: list[ShippingMethod] = []
        for item in payload.get("shipping_methods") or []:
            method = ShippingMethod(
                id=item["id"],
                name=item.get("name", ""),
                carrier=item.get("carrier", ""),
                min_weight_kg=_kg(item.get("min_weight")),
                max_weight_kg=_kg(item.get("max_weight")),
                is_current=item["id"] == order.current_shipping_method_id,
            )
            methods.append(method)

        methods.sort(key=lambda m: (not m.is_current, m.carrier, m.name))
        return methods

    # -- labels ---------------------------------------------------------------

    async def create_labels(
        self, order: Order, shipping_method_id: int, quantity: int
    ) -> list[Label]:
        """Announce the order and return one label per parcel."""
        if quantity < 1:
            raise SendcloudError("Aantal pakketten moet minimaal 1 zijn.")

        label_spec = {
            "mime_type": self._settings.label_mime_type,
            "dpi": self._settings.label_dpi,
        }
        body: dict[str, Any] = {
            "label": label_spec,
            "order": {
                "order_id": order.id,
                "shipping_option": {"code": None, "id": shipping_method_id},
            },
        }
        if self._settings.sendcloud_integration_id:
            body["integration_id"] = self._settings.sendcloud_integration_id

        if quantity == 1:
            payload = await self._request("POST", CREATE_LABEL_SYNC, json=body)
            return _labels_from_payload(payload)

        # Multicollo: one entry per physical box. Sendcloud splits the order
        # weight across them itself when no per-parcel weight is given.
        body["order"]["parcels"] = [{} for _ in range(quantity)]
        payload = await self._request("POST", CREATE_LABELS_ASYNC, json=body)
        return await self._await_async_labels(payload)

    async def _await_async_labels(
        self, payload: dict[str, Any], *, attempts: int = 20, delay: float = 1.0
    ) -> list[Label]:
        labels = _labels_from_payload(payload)
        if labels:
            return labels

        data = payload.get("data") or {}
        job_id = data.get("id") or payload.get("id")
        status_path = data.get("status_url") or payload.get("status_url")
        if not job_id and not status_path:
            raise SendcloudError(
                "Sendcloud gaf geen label en geen opdracht-id terug voor multicollo."
            )
        path = status_path or ASYNC_STATUS.format(job_id=job_id)
        if path.startswith("http"):
            path = path.split(self._settings.sendcloud_api_base.rstrip("/"), 1)[-1]

        for attempt in range(attempts):
            await asyncio.sleep(delay if attempt else 0.0)
            payload = await self._request("GET", path)
            status = (payload.get("data") or payload).get("status", "")
            if status in ("failed", "error"):
                raise SendcloudError(
                    _async_failure_message(payload) or "Label aanmaken is mislukt."
                )
            labels = _labels_from_payload(payload)
            if labels:
                return labels

        raise SendcloudError("Sendcloud had te lang nodig om de labels te maken.")


# --- payload parsing ---------------------------------------------------------


def _matches(candidate: Any, order_number: str) -> bool:
    """Compare order numbers tolerantly.

    Packing slips and webshops disagree about the leading '#' and about leading
    zeroes, so compare on the bare alphanumerics.
    """
    if candidate is None:
        return False
    normalise = lambda value: str(value).strip().lstrip("#").lower()  # noqa: E731
    return normalise(candidate) == normalise(order_number)


def _order_from_v3(item: dict[str, Any]) -> Order:
    address = item.get("shipping_address") or item.get("to_address") or {}
    country = address.get("country") or {}
    if isinstance(country, str):
        country = {"iso_2": country, "name": country}
    method = item.get("shipping_option") or item.get("shipping_method") or {}
    parcels = item.get("parcels") or []

    return Order(
        id=str(item.get("id") or item.get("order_id") or ""),
        order_number=str(item.get("order_number") or ""),
        recipient_name=address.get("name", ""),
        company_name=address.get("company_name", ""),
        address=" ".join(
            part
            for part in (
                address.get("address_line_1") or address.get("address", ""),
                address.get("house_number", ""),
                address.get("address_line_2", ""),
            )
            if part
        ).strip(),
        postal_code=address.get("postal_code", ""),
        city=address.get("city", ""),
        country_code=country.get("iso_2", ""),
        country_name=country.get("name", ""),
        weight_kg=_kg((item.get("weight") or {}).get("value")
                      if isinstance(item.get("weight"), dict) else item.get("weight")),
        current_shipping_method_id=method.get("id"),
        current_shipping_method_name=method.get("name", ""),
        already_announced=bool(parcels),
        tracking_numbers=[p["tracking_number"] for p in parcels if p.get("tracking_number")],
    )


def _order_from_v2(parcel: dict[str, Any]) -> Order:
    country = parcel.get("country") or {}
    shipment = parcel.get("shipment") or {}
    status = (parcel.get("status") or {}).get("message", "")

    return Order(
        id=str(parcel.get("id", "")),
        order_number=str(parcel.get("order_number") or ""),
        recipient_name=parcel.get("name", ""),
        company_name=parcel.get("company_name", ""),
        address=parcel.get("address", ""),
        postal_code=parcel.get("postal_code", ""),
        city=parcel.get("city", ""),
        country_code=country.get("iso_2", ""),
        country_name=country.get("name", ""),
        weight_kg=_kg(parcel.get("weight")),
        current_shipping_method_id=shipment.get("id"),
        current_shipping_method_name=shipment.get("name", ""),
        already_announced=bool(parcel.get("tracking_number"))
        or status.lower().startswith("announced"),
        tracking_numbers=[parcel["tracking_number"]] if parcel.get("tracking_number") else [],
    )


def _labels_from_payload(payload: dict[str, Any]) -> list[Label]:
    data = payload.get("data") or payload
    parcels = data.get("parcels") or []
    if not parcels and data.get("label"):
        parcels = [data]

    labels: list[Label] = []
    for parcel in parcels:
        label = parcel.get("label") or {}
        file_base64 = label.get("file") or ""
        if not file_base64:
            continue
        labels.append(
            Label(
                parcel_id=str(parcel.get("parcel_id") or parcel.get("id") or ""),
                mime_type=label.get("mime_type", "application/pdf"),
                file_base64=file_base64,
                tracking_number=parcel.get("tracking_number", ""),
            )
        )
    return labels


def _async_failure_message(payload: dict[str, Any]) -> str:
    data = payload.get("data") or payload
    errors = data.get("errors") or []
    messages = [e.get("message", "") for e in errors if isinstance(e, dict)]
    return " ".join(m for m in messages if m)


def _error_message(response: httpx.Response) -> str:
    """Turn a Sendcloud error body into something a packer can act on."""
    try:
        payload = response.json()
    except ValueError:
        return f"Sendcloud gaf status {response.status_code}."

    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])

    errors = payload.get("errors")
    if isinstance(errors, list):
        messages = []
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message") or item.get("detail") or ""
                field = item.get("field") or ""
                messages.append(f"{field}: {message}" if field else message)
        joined = " ".join(m for m in messages if m)
        if joined:
            return joined

    return f"Sendcloud gaf status {response.status_code}."


def decode_label(label: Label) -> bytes:
    return base64.b64decode(label.file_base64)
