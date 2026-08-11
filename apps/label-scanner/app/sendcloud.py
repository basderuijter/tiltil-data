"""Sendcloud client, built on the v3 Ship an Order API.

Two announcement paths, because Sendcloud splits them:

* one parcel  → ``POST /v3/orders/create-label-sync``. Returns the label inline
  as base64 in the same response, so nothing else is needed.
* more parcels → ``POST /v3/orders/create-labels-async``. Returns only parcel
  and shipment ids; the labels are fetched afterwards from
  ``GET /v3/shipments/{id}``, which carries a download link per parcel.

Both identify the order by its ``order_number`` — the number on the packing
slip — so announcing needs no id lookup. The lookup we do perform is purely to
show the address and the current shipping option on screen before the packer
commits.

Lines marked `VERIFY:` are the parts not covered by the documentation on hand.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx

from .config import Settings
from .models import Label, Order, OrderItem, ShippingOption

# --- Endpoints ---------------------------------------------------------------
CREATE_LABEL_SYNC = "/v3/orders/create-label-sync"
CREATE_LABELS_ASYNC = "/v3/orders/create-labels-async"
SHIPMENT = "/v3/shipments/{shipment_id}"
ORDER = "/v3/orders/{order_id}"
CONTRACTS = "/v3/contracts"
# VERIFY: the list-orders-per-integration path and its search parameter.
ORDERS_SEARCH = "/v3/orders"
# VERIFY: shipping options is a POST that takes the route; shape unconfirmed.
SHIPPING_OPTIONS = "/v3/shipping-options"
# Fallback lookup: the long-standing v2 parcels endpoint.
PARCELS_SEARCH_V2 = "/v2/parcels"

# How often to re-ask while waiting for a freshly created order to appear.
LOOKUP_RETRY_INTERVAL = 1.0


class SendcloudError(Exception):
    """Raised for any Sendcloud failure the operator should see."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class OrderNotFound(SendcloudError):
    pass


class SendcloudClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    # -- plumbing -------------------------------------------------------------

    async def _request(
        self, method: str, path: str, *, raw: bool = False, **kwargs: Any
    ) -> Any:
        url = path if path.startswith("http") else (
            f"{self._settings.sendcloud_api_base.rstrip('/')}{path}"
        )
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
        if raw:
            return response.content
        if not response.content:
            return {}
        return response.json()

    # -- orders ---------------------------------------------------------------

    async def find_order(self, order_number: str) -> Order:
        """Look up a scanned order number, trying v3 first and then v2.

        Sendcloud saves orders asynchronously: a 201 from the Orders API does
        not mean the order can be shipped yet. The delivery is created moments
        before the packing slip reaches the packing table, so a first miss is
        expected rather than exceptional — keep asking briefly before giving up.
        """
        order_number = order_number.strip()
        if not order_number:
            raise OrderNotFound("Geen ordernummer gescand.")

        deadline = self._settings.lookup_retry_seconds
        waited = 0.0
        while True:
            order = await self._find_order_v3(order_number)
            if order is None:
                order = await self._find_order_v2(order_number)
            if order is not None:
                return order
            if waited >= deadline:
                raise OrderNotFound(f"Order {order_number} niet gevonden in Sendcloud.")
            await asyncio.sleep(LOOKUP_RETRY_INTERVAL)
            waited += LOOKUP_RETRY_INTERVAL

    async def _find_order_v3(self, order_number: str) -> Order | None:
        params: dict[str, Any] = {"order_number": order_number}
        if self._settings.sendcloud_integration_id:
            params["integration_id"] = self._settings.sendcloud_integration_id
        try:
            payload = await self._request("GET", ORDERS_SEARCH, params=params)
        except SendcloudError as exc:
            # A 4xx here means "this search is not available as assumed", not
            # "no such order" — fall through to v2 rather than failing the scan.
            if exc.status_code in (400, 404, 405, 501):
                return None
            raise

        items = payload.get("data") or []
        if isinstance(items, dict):
            items = [items]
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

    # -- shipping options -----------------------------------------------------

    async def shipping_options(self, order: Order) -> list[ShippingOption]:
        """The options the packer can choose from.

        Falls back to the locally configured list when the API lookup is not
        wired up, so the tool keeps working with the handful of options a
        warehouse actually uses.
        """
        options = await self._shipping_options_from_api(order)
        if options is None:
            options = self._shipping_options_from_file()

        for option in options:
            option.is_current = option.code == order.current_shipping_option_code
        options.sort(key=lambda o: (not o.is_current, o.carrier, o.name))
        return options

    async def _shipping_options_from_api(self, order: Order) -> list[ShippingOption] | None:
        body = {
            "to_address": {"country_code": order.country_code},
            "weight": {"value": f"{order.weight_kg or 1:.3f}", "unit": "kg"},
        }
        try:
            payload = await self._request("POST", SHIPPING_OPTIONS, json=body)
        except SendcloudError as exc:
            if exc.status_code in (400, 404, 405, 501):
                return None
            raise

        options = []
        for item in payload.get("data") or []:
            code = item.get("code")
            if not code:
                continue
            carrier = item.get("carrier") or {}
            options.append(
                ShippingOption(
                    code=code,
                    name=item.get("name") or code,
                    carrier=carrier.get("name") or carrier.get("code") or "",
                    contract_id=(item.get("contract") or {}).get("id"),
                )
            )
        return options or None

    def _shipping_options_from_file(self) -> list[ShippingOption]:
        path = self._settings.shipping_options_file
        if not path or not path.exists():
            raise SendcloudError(
                "Geen verzendmethodes beschikbaar. Zet SHIPPING_OPTIONS_FILE in "
                ".env of maak de shipping-options API bereikbaar."
            )
        try:
            entries = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise SendcloudError(f"Kan {path} niet lezen: {exc}") from exc
        return [ShippingOption(**entry) for entry in entries]

    # -- labels ---------------------------------------------------------------

    async def create_labels(
        self, order: Order, shipping_option_code: str, quantity: int
    ) -> list[Label]:
        """Announce the order and return one label per parcel."""
        if quantity < 1:
            raise SendcloudError("Aantal pakketten moet minimaal 1 zijn.")

        if quantity == 1:
            return await self._create_label_sync(order, shipping_option_code)
        return await self._create_labels_async(order, shipping_option_code, quantity)

    def _base_body(self, shipping_option_code: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "integration_id": self._settings.sendcloud_integration_id,
            "ship_with": {
                "type": "shipping_option_code",
                "properties": {
                    "shipping_option_code": shipping_option_code,
                    "contract_id": self._settings.contract_id,
                },
            },
        }
        return body

    async def _create_label_sync(
        self, order: Order, shipping_option_code: str
    ) -> list[Label]:
        body = self._base_body(shipping_option_code)
        body["label_details"] = {
            "mime_type": self._settings.label_mime_type,
            "dpi": self._settings.label_dpi,
        }
        body["order"] = {
            "order_number": order.order_number,
            "apply_shipping_rules": self._settings.apply_shipping_rules,
        }
        payload = await self._request("POST", CREATE_LABEL_SYNC, json=body)

        # The documented schema and its own example disagree on whether `data`
        # is an object or a list, and on whether the file sits under
        # `label_details` or `label`. Accept all four shapes.
        entries = payload.get("data")
        if isinstance(entries, dict):
            entries = [entries]
        labels = [label for entry in entries or [] if (label := _inline_label(entry))]
        if not labels:
            raise SendcloudError("Sendcloud gaf geen labelbestand terug.")
        return labels

    async def _create_labels_async(
        self, order: Order, shipping_option_code: str, quantity: int
    ) -> list[Label]:
        items = order.items
        if not items and order.id:
            # The lookup did not carry the item lines; fetch them, because a
            # multicollo announcement has to say what is in each box.
            items = await self.order_items(order.id)

        body = self._base_body(shipping_option_code)
        body["orders"] = [
            {
                "order_number": order.order_number,
                "apply_shipping_rules": self._settings.apply_shipping_rules,
                "parcels": split_items(items, quantity),
            }
        ]
        payload = await self._request("POST", CREATE_LABELS_ASYNC, json=body)

        _raise_for_partial_failure(payload)
        entries = payload.get("data") or []
        if not entries:
            raise SendcloudError("Sendcloud maakte geen zending aan voor deze order.")
        shipment_id = entries[0].get("shipment_id")
        if not shipment_id:
            raise SendcloudError("Sendcloud gaf geen shipment_id terug.")
        return await self._labels_from_shipment(str(shipment_id), quantity)

    async def _labels_from_shipment(
        self, shipment_id: str, expected: int, *, attempts: int = 20, delay: float = 1.0
    ) -> list[Label]:
        """Wait for the async announcement, then download each label."""
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(delay)
            payload = await self._request("GET", SHIPMENT.format(shipment_id=shipment_id))
            data = payload.get("data") or {}

            if errors := data.get("errors"):
                raise SendcloudError(_join_errors(errors))

            parcels = data.get("parcels") or []
            ready = [p for p in parcels if _label_link(p)]
            if len(ready) >= expected:
                return [await self._download_label(parcel) for parcel in ready]

        raise SendcloudError("Sendcloud had te lang nodig om de labels te maken.")

    async def _download_label(self, parcel: dict[str, Any]) -> Label:
        link = _label_link(parcel)
        # VERIFY: the document link accepts the format as query parameters.
        # Sendcloud documents it as "allows downloading in PDF, PNG and ZPL and
        # various DPI" without naming them; a plain GET returns the default.
        params = {
            "mime_type": self._settings.label_mime_type,
            "dpi": self._settings.label_dpi,
        }
        try:
            content = await self._request("GET", link, params=params, raw=True)
        except SendcloudError:
            content = await self._request("GET", link, raw=True)

        return Label(
            parcel_id=str(parcel.get("id", "")),
            mime_type=self._settings.label_mime_type,
            file_base64=base64.b64encode(content).decode(),
            tracking_number=parcel.get("tracking_number", ""),
        )

    async def contracts(self) -> list[dict[str, Any]]:
        """List the account's carrier contracts.

        Only needed during setup, to find a CONTRACT_ID. Leaving that unset is
        fine when there is one default contract per carrier — Sendcloud then
        picks it automatically.
        """
        payload = await self._request("GET", CONTRACTS, params={"is_active": True})
        return [
            {
                "id": contract.get("id"),
                "carrier": (contract.get("carrier") or {}).get("name", ""),
                "name": contract.get("name", ""),
                "country_code": contract.get("country_code", ""),
                "is_default_per_carrier": contract.get("is_default_per_carrier", False),
                "state": contract.get("state", ""),
            }
            for contract in payload.get("data") or []
        ]

    async def order_items(self, order_id: str) -> list[OrderItem]:
        """Fetch the items of an order, needed to split a multicollo shipment."""
        payload = await self._request("GET", ORDER.format(order_id=order_id))
        data = payload.get("data") or {}
        return _items_from_order(data)


# --- multicollo --------------------------------------------------------------


def split_items(items: list[OrderItem], quantity: int) -> list[dict[str, Any]]:
    """Spread the order's items over `quantity` boxes.

    Sendcloud requires every parcel of a multicollo shipment to declare which
    items it holds. The packer only tells us how many boxes there are, so we
    distribute the units round-robin: the split is arbitrary but complete, which
    is what the carrier needs for the customs and weight totals to add up.
    """
    if not items:
        raise SendcloudError(
            "Deze order heeft geen artikelregels in Sendcloud, dus kan niet over "
            "meerdere pakketten verdeeld worden. Verzend als één pakket."
        )

    units: list[str] = []
    for item in items:
        units.extend([item.item_id] * max(item.quantity, 1))

    if len(units) < quantity:
        raise SendcloudError(
            f"Deze order heeft {len(units)} artikel(en), dus kunnen er geen "
            f"{quantity} pakketten van gemaakt worden."
        )

    buckets: list[dict[str, int]] = [{} for _ in range(quantity)]
    for index, item_id in enumerate(units):
        bucket = buckets[index % quantity]
        bucket[item_id] = bucket.get(item_id, 0) + 1

    return [
        {
            "parcel_items": [
                {"item_id": item_id, "quantity": count}
                for item_id, count in bucket.items()
            ]
        }
        for bucket in buckets
    ]


# --- payload parsing ---------------------------------------------------------


def _matches(candidate: Any, order_number: str) -> bool:
    """Compare order numbers tolerantly.

    Packing slips and webshops disagree about the leading '#', so compare
    without it.
    """
    if candidate is None:
        return False
    normalise = lambda value: str(value).strip().lstrip("#").lower()  # noqa: E731
    return normalise(candidate) == normalise(order_number)


def _kg(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _items_from_order(data: dict[str, Any]) -> list[OrderItem]:
    raw = (data.get("order_details") or {}).get("order_items") or []
    items = []
    for entry in raw:
        item_id = entry.get("item_id")
        if not item_id:
            continue
        items.append(
            OrderItem(
                item_id=str(item_id),
                quantity=int(entry.get("quantity") or 1),
                name=entry.get("name", ""),
            )
        )
    return items


def _order_from_v3(item: dict[str, Any]) -> Order:
    address = item.get("shipping_address") or {}
    shipping = item.get("shipping_details") or {}
    ship_with = (shipping.get("ship_with") or {}).get("properties") or {}
    weight = (shipping.get("measurement") or {}).get("weight") or {}

    return Order(
        id=str(item.get("id") or ""),
        order_number=str(item.get("order_number") or ""),
        recipient_name=address.get("name", ""),
        company_name=address.get("company_name", ""),
        address=" ".join(
            part
            for part in (
                address.get("address_line_1", ""),
                address.get("house_number", ""),
                address.get("address_line_2", ""),
            )
            if part
        ).strip(),
        postal_code=address.get("postal_code", ""),
        city=address.get("city", ""),
        country_code=address.get("country_code", ""),
        country_name=address.get("country_code", ""),
        weight_kg=_kg(weight.get("value")),
        current_shipping_option_code=ship_with.get("shipping_option_code") or "",
        # The webshop's own wording for the chosen method, which is often more
        # recognisable to a packer than the Sendcloud code.
        current_shipping_option_name=shipping.get("delivery_indicator", ""),
        items=_items_from_order(item),
    )


def _order_from_v2(parcel: dict[str, Any]) -> Order:
    country = parcel.get("country") or {}
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
        # v2 names the method but not the v3 code, so nothing is preselected.
        current_shipping_option_name=(parcel.get("shipment") or {}).get("name", ""),
        already_announced=bool(parcel.get("tracking_number"))
        or status.lower().startswith("announced"),
        tracking_numbers=[parcel["tracking_number"]] if parcel.get("tracking_number") else [],
    )


def _inline_label(entry: dict[str, Any]) -> Label | None:
    label = entry.get("label_details") or entry.get("label") or {}
    file_base64 = label.get("file")
    if not file_base64:
        return None
    return Label(
        parcel_id=str(entry.get("parcel_id") or ""),
        mime_type=label.get("mime_type", "application/pdf"),
        file_base64=file_base64,
        tracking_number=entry.get("tracking_number", ""),
    )


def _label_link(parcel: dict[str, Any]) -> str:
    for document in parcel.get("documents") or []:
        kind = document.get("document_type") or document.get("type")
        if kind == "label" and document.get("link"):
            return document["link"]
    return ""


def _raise_for_partial_failure(payload: dict[str, Any]) -> None:
    """The async endpoint fails gracefully, returning errors alongside data."""
    if errors := payload.get("errors"):
        raise SendcloudError(_join_errors(errors))


def _join_errors(errors: Any) -> str:
    if isinstance(errors, dict):
        errors = [errors]
    messages = [
        e.get("detail") or e.get("title") or ""
        for e in errors or []
        if isinstance(e, dict)
    ]
    return " ".join(m for m in messages if m) or "Sendcloud meldde een fout."


def _error_message(response: httpx.Response) -> str:
    """Turn a Sendcloud error body into something a packer can act on."""
    try:
        payload = response.json()
    except ValueError:
        return f"Sendcloud gaf status {response.status_code}."

    if errors := payload.get("errors"):
        return _join_errors(errors)

    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])

    return f"Sendcloud gaf status {response.status_code}."


def decode_label(label: Label) -> bytes:
    return base64.b64decode(label.file_base64)
