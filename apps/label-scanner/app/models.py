"""Domain models shared between the Sendcloud client, the API and the UI."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShippingMethod(BaseModel):
    id: int
    name: str
    carrier: str = ""
    min_weight_kg: float | None = None
    max_weight_kg: float | None = None
    # True for the method that is already on the order in Sendcloud.
    is_current: bool = False


class Order(BaseModel):
    """An order imported into Sendcloud that has not been announced yet."""

    # Sendcloud's own identifier, used when requesting the label.
    id: str
    # The human number on the packing slip, i.e. what gets scanned.
    order_number: str
    recipient_name: str = ""
    company_name: str = ""
    address: str = ""
    postal_code: str = ""
    city: str = ""
    country_code: str = ""
    country_name: str = ""
    weight_kg: float | None = None
    # The shipping method that came in with the order. May be absent when the
    # webshop only sent a free-text shipping name.
    current_shipping_method_id: int | None = None
    current_shipping_method_name: str = ""
    # Set when the order already has a label, so the UI can warn before
    # accidentally announcing it twice.
    already_announced: bool = False
    tracking_numbers: list[str] = Field(default_factory=list)

    @property
    def address_lines(self) -> list[str]:
        lines = [self.recipient_name]
        if self.company_name:
            lines.append(self.company_name)
        lines.append(self.address)
        lines.append(f"{self.postal_code} {self.city}".strip())
        lines.append(self.country_name or self.country_code)
        return [line for line in lines if line]


class Label(BaseModel):
    """A single printable label returned by Sendcloud."""

    parcel_id: str
    mime_type: str
    # Base64 payload exactly as Sendcloud returned it.
    file_base64: str
    tracking_number: str = ""


class LabelResult(BaseModel):
    order_number: str
    labels: list[Label]
    shipping_method_name: str
    printed: bool = False
    print_error: str = ""

    @property
    def tracking_numbers(self) -> list[str]:
        return [label.tracking_number for label in self.labels if label.tracking_number]
