"""Reporting a finished label back to the system that owns the order.

A tracking number is where the warehouse touches the customer: it drives the
Shopify fulfilment, and with it the "your parcel is on its way" mail and the
order status page. With partial deliveries that matters more, not less — the
customer has to be able to see that one delivery is under way and another is
still to come.

This app is not the right place to decide any of that; it only knows what it
just printed. So it announces the result and lets the owning system act on it.
A failed announcement never fails the label — the parcel is real and the packer
cannot fix an integration — but it is logged loudly, because a lost tracking
number is a customer who is left in the dark.
"""

from __future__ import annotations

import logging

import httpx

from .config import Settings
from .models import LabelResult

logger = logging.getLogger("label_scanner.callback")


class CallbackError(Exception):
    pass


def build_payload(result: LabelResult, station_id: str | None) -> dict:
    return {
        "order_number": result.order_number,
        "shipping_option_name": result.shipping_option_name,
        "shipping_option_code": result.shipping_option_code,
        # Without a carrier, Shopify registers the tracking number but shows no
        # link — the customer is left with a code they cannot click.
        "carrier": result.carrier,
        "station": station_id,
        "printed": result.printed,
        "parcels": [
            {"parcel_id": label.parcel_id, "tracking_number": label.tracking_number}
            for label in result.labels
        ],
    }


async def announce(
    settings: Settings,
    client: httpx.AsyncClient,
    result: LabelResult,
    station_id: str | None,
) -> None:
    if not settings.callback_url:
        return

    headers = {}
    if settings.callback_token:
        headers["Authorization"] = f"Bearer {settings.callback_token}"

    try:
        response = await client.post(
            settings.callback_url,
            json=build_payload(result, station_id),
            headers=headers,
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise CallbackError(f"Kon het label niet doorgeven: {exc}") from exc

    if response.status_code >= 400:
        raise CallbackError(
            f"Doorgeven van het label is geweigerd (status {response.status_code})."
        )
