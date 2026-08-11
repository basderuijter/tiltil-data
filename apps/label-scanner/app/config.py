"""Configuration for the warehouse label scanner.

All settings are read from environment variables (or a .env file next to the
app). See .env.example for a documented starting point.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PrintBackend = Literal["sendcloud_client", "cups", "none"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Sendcloud credentials -------------------------------------------------
    sendcloud_public_key: str = ""
    sendcloud_secret_key: str = ""
    # The integration the orders are imported through (Shopify). Find it under
    # Settings > Integrations in the Sendcloud panel; the id is in the URL.
    sendcloud_integration_id: int = 0
    sendcloud_api_base: str = "https://panel.sendcloud.sc/api"
    sendcloud_timeout_seconds: float = 30.0

    # --- Label format ----------------------------------------------------------
    # application/pdf for a normal or A6 label printer, application/zpl for a
    # Zebra. ZPL is passed through to the printer untouched.
    label_mime_type: str = "application/pdf"
    # Sendcloud only accepts specific DPI values per format: 72 for PDF,
    # 150/300 for PNG. ZPL ignores it — the carrier decides, usually 203.
    label_dpi: int = 72

    # --- Shipping --------------------------------------------------------------
    # Let Sendcloud apply the shipping rules configured in the panel. Leave off
    # when the packer's choice on screen should always win.
    apply_shipping_rules: bool = False
    # Optional: pin a carrier contract. Unset lets Sendcloud pick the default
    # contract for the carrier behind the chosen shipping option.
    contract_id: int | None = None
    # Fallback list of shipping options, used when the shipping-options API is
    # not reachable. Also the source for the printed barcode command sheet.
    # JSON: [{"code": "postnl:standard", "name": "PostNL Standard",
    #         "carrier": "postnl"}, ...]
    shipping_options_file: Path | None = None

    # --- Printing --------------------------------------------------------------
    print_backend: PrintBackend = "sendcloud_client"
    # The Sendcloud Print Client exposes a local HTTP API on the machine it runs
    # on. When the scanner runs on a Pi and the Print Client on the warehouse PC,
    # point this at that PC instead of localhost.
    print_client_url: str = "http://127.0.0.1:1903"
    print_client_printer_id: str = ""
    # Used when print_backend == "cups".
    cups_printer: str = ""
    # Multi-table setups: JSON file describing each packing table and the CUPS
    # host driving its label printer. See stations.example.json. Leave unset
    # when a single table is served.
    stations_file: Path | None = None
    spool_dir: Path = Path("/var/tmp/label-scanner")

    # --- Reporting back --------------------------------------------------------
    # Where to announce a finished label: the tracking numbers are what the
    # customer eventually sees, so whichever system owns the Shopify
    # fulfilment needs them. Left empty, nothing is reported.
    callback_url: str = ""
    # Sent as `Authorization: Bearer <token>` when set.
    callback_token: str = ""

    # --- Behaviour -------------------------------------------------------------
    # How many parcel-count buttons to render on the touch screen.
    max_parcels: int = Field(default=6, ge=1, le=20)
    # Serve fake orders and skip every outbound call. Lets you test the touch
    # screen, scanner and layout without touching the live Sendcloud account.
    demo_mode: bool = False

    @property
    def has_credentials(self) -> bool:
        return bool(self.sendcloud_public_key and self.sendcloud_secret_key)

    @model_validator(mode="after")
    def _check_label_format(self) -> Settings:
        """Reject DPI/format combinations Sendcloud would answer with a 400."""
        allowed = {"application/pdf": {72}, "image/png": {150, 300}}
        valid = allowed.get(self.label_mime_type)
        if valid and self.label_dpi not in valid:
            raise ValueError(
                f"LABEL_DPI={self.label_dpi} is not valid for "
                f"{self.label_mime_type}; Sendcloud accepts "
                f"{sorted(valid)}. ZPL ignores the DPI setting."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
