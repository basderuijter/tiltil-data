"""Configuration for the warehouse label scanner.

All settings are read from environment variables (or a .env file next to the
app). See .env.example for a documented starting point.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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
    label_dpi: int = 203

    # --- Printing --------------------------------------------------------------
    print_backend: PrintBackend = "sendcloud_client"
    # The Sendcloud Print Client exposes a local HTTP API on the machine it runs
    # on. When the scanner runs on a Pi and the Print Client on the warehouse PC,
    # point this at that PC instead of localhost.
    print_client_url: str = "http://127.0.0.1:1903"
    print_client_printer_id: str = ""
    # Used when print_backend == "cups".
    cups_printer: str = ""
    spool_dir: Path = Path("/var/tmp/label-scanner")

    # --- Behaviour -------------------------------------------------------------
    # How many parcel-count buttons to render on the touch screen.
    max_parcels: int = Field(default=6, ge=1, le=20)
    # Serve fake orders and skip every outbound call. Lets you test the touch
    # screen, scanner and layout without touching the live Sendcloud account.
    demo_mode: bool = False

    @property
    def has_credentials(self) -> bool:
        return bool(self.sendcloud_public_key and self.sendcloud_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
