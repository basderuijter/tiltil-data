"""Fake Sendcloud responses for demo mode.

Lets you set up the touch screen, test the barcode scanner and train packers
without creating real labels. Enable with DEMO_MODE=true.
"""

from __future__ import annotations

import base64

from .models import Label, Order, ShippingMethod

# A valid, minimal one-page PDF so the print path can be exercised end to end.
_DEMO_PDF = base64.b64encode(
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 288 432]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
).decode()

_METHODS = [
    ShippingMethod(id=8, name="Unstamped letter", carrier="postnl"),
    ShippingMethod(id=1675, name="PostNL Standard 0-23kg", carrier="postnl"),
    ShippingMethod(id=1693, name="PostNL Pakje Gemak 0-23kg", carrier="postnl"),
    ShippingMethod(id=2100, name="DHL Parcel Connect 0-31.5kg", carrier="dhl"),
]


def demo_order(order_number: str) -> Order:
    return Order(
        id=f"demo-{order_number}",
        order_number=order_number,
        recipient_name="Demo Klant",
        address="Voorbeeldstraat 12",
        postal_code="5611 AA",
        city="Eindhoven",
        country_code="NL",
        country_name="Netherlands",
        weight_kg=2.5,
        current_shipping_method_id=1675,
        current_shipping_method_name="PostNL Standard 0-23kg",
    )


def demo_shipping_methods(order: Order) -> list[ShippingMethod]:
    methods = [
        method.model_copy(update={"is_current": method.id == order.current_shipping_method_id})
        for method in _METHODS
    ]
    methods.sort(key=lambda m: (not m.is_current, m.carrier, m.name))
    return methods


def demo_labels(order: Order, quantity: int) -> list[Label]:
    return [
        Label(
            parcel_id=f"{order.id}-{index + 1}",
            mime_type="application/pdf",
            file_base64=_DEMO_PDF,
            tracking_number=f"3SDEMO{index + 1:08d}",
        )
        for index in range(quantity)
    ]
