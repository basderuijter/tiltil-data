"""Fake Sendcloud responses for demo mode.

Lets you set up the touch screen, test the barcode scanner and train packers
without creating real labels. Enable with DEMO_MODE=true.
"""

from __future__ import annotations

import base64

from .models import Label, Order, OrderItem, ShippingOption

# A valid, minimal one-page PDF so the print path can be exercised end to end.
_DEMO_PDF = base64.b64encode(
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 288 432]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
).decode()

_OPTIONS = [
    ShippingOption(code="postnl:letterbox", name="PostNL Brievenbuspakket", carrier="postnl"),
    ShippingOption(code="postnl:standard", name="PostNL Standard", carrier="postnl"),
    ShippingOption(code="postnl:service_point", name="PostNL Pakje Gemak", carrier="postnl"),
    ShippingOption(code="dhl:parcel_connect", name="DHL Parcel Connect", carrier="dhl"),
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
        current_shipping_option_code="postnl:standard",
        current_shipping_option_name="PostNL Standard",
        items=[
            OrderItem(item_id="5552", quantity=2, name="Cylinder candle"),
            OrderItem(item_id="5555", quantity=4, name="Linnen kussen"),
        ],
    )


def demo_shipping_options(order: Order) -> list[ShippingOption]:
    options = [
        option.model_copy(
            update={"is_current": option.code == order.current_shipping_option_code}
        )
        for option in _OPTIONS
    ]
    options.sort(key=lambda o: (not o.is_current, o.carrier, o.name))
    return options


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
