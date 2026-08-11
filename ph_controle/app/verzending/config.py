"""Sendcloud- en printinstellingen.

Overgenomen uit de losse label-scanner (`app/config.py` daar), maar zonder de
webapp-instellingen die met die tool zijn vervallen. De veldnamen zijn
hetzelfde gebleven zodat de verhuisde modules ongewijzigd konden meekomen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASIS = Path(__file__).resolve().parent.parent.parent

# Sendcloud accepteert per formaat maar een paar dpi-waarden; een verkeerde
# combinatie geeft een 400. Liever bij het opstarten falen dan bij het eerste
# label van de dag.
TOEGESTANE_DPI = {"application/pdf": {72}, "image/png": {150, 300}}


def _bool(naam: str, standaard: bool) -> bool:
    waarde = os.environ.get(naam)
    if waarde is None:
        return standaard
    return waarde.strip().lower() in ("1", "true", "ja", "yes", "on")


def _pad(naam: str) -> Path | None:
    waarde = os.environ.get(naam, "").strip()
    return Path(waarde) if waarde else None


def _int_of_niets(naam: str) -> int | None:
    waarde = os.environ.get(naam, "").strip()
    return int(waarde) if waarde else None


class VerzendFout(ValueError):
    """Onbruikbare verzendinstelling."""


@dataclass
class VerzendInstellingen:
    # --- Sendcloud ---
    sendcloud_public_key: str = field(
        default_factory=lambda: os.environ.get("SENDCLOUD_PUBLIC_KEY", "")
    )
    sendcloud_secret_key: str = field(
        default_factory=lambda: os.environ.get("SENDCLOUD_SECRET_KEY", "")
    )
    # De integratie waar de orders doorheen binnenkomen (Shopify). Het id staat
    # in de URL onder Settings > Integrations in het Sendcloud-paneel.
    sendcloud_integration_id: int = field(
        default_factory=lambda: int(os.environ.get("SENDCLOUD_INTEGRATION_ID", "0") or 0)
    )
    sendcloud_api_base: str = field(
        default_factory=lambda: os.environ.get(
            "SENDCLOUD_API_BASE", "https://panel.sendcloud.sc/api"
        )
    )
    sendcloud_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("SENDCLOUD_TIMEOUT_SECONDS", "30"))
    )
    # Sendcloud slaat orders asynchroon op: een 201 betekent niet dat de order
    # verzendklaar is. Zo lang blijven vragen voordat we "niet gevonden" melden.
    lookup_retry_seconds: float = field(
        default_factory=lambda: float(os.environ.get("LOOKUP_RETRY_SECONDS", "6"))
    )

    # --- Labelformaat ---
    # application/pdf voor een gewone of A6-labelprinter, application/zpl voor
    # een Zebra. ZPL gaat ongewijzigd naar de printer.
    label_mime_type: str = field(
        default_factory=lambda: os.environ.get("LABEL_MIME_TYPE", "application/pdf")
    )
    label_dpi: int = field(default_factory=lambda: int(os.environ.get("LABEL_DPI", "72")))

    # --- Verzending ---
    apply_shipping_rules: bool = field(
        default_factory=lambda: _bool("APPLY_SHIPPING_RULES", False)
    )
    contract_id: int | None = field(default_factory=lambda: _int_of_niets("CONTRACT_ID"))
    # Terugvallijst met verzendmethodes voor als de shipping-options API niet
    # bereikbaar is. Zie config/shipping-options.example.json.
    shipping_options_file: Path | None = field(
        default_factory=lambda: _pad("SHIPPING_OPTIONS_FILE")
    )
    # Verzendmethode als de order er zelf geen heeft.
    standaard_verzendmethode: str = field(
        default_factory=lambda: os.environ.get("STANDAARD_VERZENDMETHODE", "")
    )
    # Regels die hierop matchen krijgen geen plek in de zending: giftcards
    # worden in Shopify al digitaal afgemeld en zouden anders dubbel gaan.
    giftcard_patroon: str = field(
        default_factory=lambda: os.environ.get(
            "GIFTCARD_PATROON", r"giftcard|gift card|cadeaubon|kadobon|e-?voucher"
        )
    )

    # --- Printen ---
    print_backend: str = field(
        default_factory=lambda: os.environ.get("PRINT_BACKEND", "sendcloud_client")
    )
    print_client_url: str = field(
        default_factory=lambda: os.environ.get("PRINT_CLIENT_URL", "http://127.0.0.1:1903")
    )
    print_client_printer_id: str = field(
        default_factory=lambda: os.environ.get("PRINT_CLIENT_PRINTER_ID", "")
    )
    cups_printer: str = field(default_factory=lambda: os.environ.get("CUPS_PRINTER", ""))
    # Meerdere PH-tafels: JSON-bestand met per tafel de CUPS-host die zijn
    # labelprinter aanstuurt. Zie config/stations.example.json.
    stations_file: Path | None = field(default_factory=lambda: _pad("STATIONS_FILE"))
    spool_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("SPOOL_DIR", "/var/tmp/ph-labels"))
    )

    # --- Gedrag ---
    # Nepdata en geen enkele uitgaande call: om te oefenen zonder echte labels.
    demo_mode: bool = field(default_factory=lambda: _bool("VERZENDING_DEMO", False))

    def __post_init__(self) -> None:
        toegestaan = TOEGESTANE_DPI.get(self.label_mime_type)
        if toegestaan and self.label_dpi not in toegestaan:
            raise VerzendFout(
                f"LABEL_DPI={self.label_dpi} kan niet met {self.label_mime_type}; "
                f"Sendcloud accepteert {sorted(toegestaan)}. ZPL negeert de dpi."
            )
        if self.print_backend not in ("sendcloud_client", "cups", "none"):
            raise VerzendFout(
                f"PRINT_BACKEND '{self.print_backend}' bestaat niet "
                "(sendcloud_client, cups of none)."
            )

    @property
    def has_credentials(self) -> bool:
        return bool(self.sendcloud_public_key and self.sendcloud_secret_key)

    @property
    def actief(self) -> bool:
        """Kan de app labels maken — echt of in demomodus?"""
        return self.demo_mode or self.has_credentials


def laad_verzendinstellingen() -> VerzendInstellingen:
    return VerzendInstellingen()
