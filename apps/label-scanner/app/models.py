"""Domain models shared between the Sendcloud client, the API and the UI."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShippingOption(BaseModel):
    """A way to ship, identified by its v3 shipping_option_code."""

    # e.g. "postnl:standard" or "postnl:letterbox". This is what the API wants,
    # and what the barcode on the command sheet encodes.
    code: str
    name: str
    carrier: str = ""
    # Optional: Sendcloud picks the default contract for the carrier when unset.
    contract_id: int | None = None
    # True for the option that is already on the order in Sendcloud.
    is_current: bool = False


class OrderItem(BaseModel):
    """One product line, needed to split a multicollo shipment across boxes."""

    item_id: str
    quantity: int = 1
    name: str = ""


class Order(BaseModel):
    """An order imported into Sendcloud that has not been announced yet."""

    # Sendcloud's own identifier.
    id: str
    # The human number on the packing slip, i.e. what gets scanned. This is
    # also what identifies the order when requesting a label, so no id lookup
    # is needed to announce.
    order_number: str
    recipient_name: str = ""
    company_name: str = ""
    address: str = ""
    postal_code: str = ""
    city: str = ""
    country_code: str = ""
    country_name: str = ""
    weight_kg: float | None = None
    # The shipping option that came in with the order.
    current_shipping_option_code: str = ""
    current_shipping_option_name: str = ""
    items: list[OrderItem] = Field(default_factory=list)
    # Set when the order already has a label, so the UI can warn before
    # accidentally announcing it twice.
    already_announced: bool = False
    tracking_numbers: list[str] = Field(default_factory=list)

    @property
    def total_units(self) -> int:
        return sum(item.quantity for item in self.items)


class Label(BaseModel):
    """A single printable label."""

    parcel_id: str
    mime_type: str
    # Base64 payload, either as returned inline by Sendcloud or as downloaded
    # from the parcel's document link.
    file_base64: str
    tracking_number: str = ""


class LabelResult(BaseModel):
    order_number: str
    labels: list[Label]
    shipping_option_name: str
    printed: bool = False
    print_error: str = ""
    # Whether the owning system was told about this label. False here means the
    # customer may never get a tracking mail, so it is worth monitoring.
    reported: bool = True
    report_error: str = ""
