"""Instellingen, volledig via omgevingsvariabelen (zie .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .verzending.config import VerzendInstellingen, laad_verzendinstellingen

BASIS = Path(__file__).resolve().parent.parent


def _bool(naam: str, standaard: bool) -> bool:
    waarde = os.environ.get(naam)
    if waarde is None:
        return standaard
    return waarde.strip().lower() in ("1", "true", "ja", "yes", "on")


@dataclass
class Instellingen:
    # mock = draaien op de demofixture, rest = de echte SRS-webservice
    srs_backend: str = field(default_factory=lambda: os.environ.get("SRS_BACKEND", "mock"))
    srs_base_url: str = field(default_factory=lambda: os.environ.get("SRS_BASE_URL", ""))
    srs_auth_type: str = field(
        default_factory=lambda: os.environ.get("SRS_AUTH_TYPE", "none")
    )  # none | basic | bearer | header
    srs_gebruiker: str = field(default_factory=lambda: os.environ.get("SRS_USERNAME", ""))
    srs_wachtwoord: str = field(default_factory=lambda: os.environ.get("SRS_PASSWORD", ""))
    srs_token: str = field(default_factory=lambda: os.environ.get("SRS_TOKEN", ""))
    srs_api_key_header: str = field(
        default_factory=lambda: os.environ.get("SRS_API_KEY_HEADER", "X-Api-Key")
    )
    srs_timeout: float = field(
        default_factory=lambda: float(os.environ.get("SRS_TIMEOUT", "20"))
    )
    srs_verify_ssl: bool = field(default_factory=lambda: _bool("SRS_VERIFY_SSL", True))
    srs_mapping_bestand: Path = field(
        default_factory=lambda: Path(
            os.environ.get("SRS_MAPPING_FILE", BASIS / "config" / "srs_rest.json")
        )
    )
    fixture_bestand: Path = field(
        default_factory=lambda: Path(
            os.environ.get("SRS_FIXTURE_FILE", BASIS / "fixtures" / "srs_demo.json")
        )
    )
    database: Path = field(
        default_factory=lambda: Path(os.environ.get("PH_DB", BASIS / "data" / "controles.db"))
    )
    hal_bestand: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HAL_INDELING", BASIS / "config" / "hal.json")
        )
    )

    # --- Signalering: wanneer vraagt een PH aandacht of loopt hij vast? ---
    letop_uren: float = field(default_factory=lambda: float(os.environ.get("PH_LETOP_UREN", "4")))
    vastloper_uren: float = field(
        default_factory=lambda: float(os.environ.get("PH_VASTLOPER_UREN", "24"))
    )
    stilstand_uren: float = field(
        default_factory=lambda: float(os.environ.get("PH_STILSTAND_UREN", "8"))
    )

    # --- Snelheid ---
    # Laatste scan = klaar: akkoord, SRS-afmelding en pakbon gaan vanzelf.
    snelmodus: bool = field(default_factory=lambda: _bool("PH_SNELMODUS", True))
    # Controlescherm meteen openen als de medewerker bekend is.
    auto_start: bool = field(default_factory=lambda: _bool("PH_AUTO_START", True))

    # --- Verzending (Sendcloud + labelprinter) ---
    verzending: VerzendInstellingen = field(default_factory=laad_verzendinstellingen)
    # Welke PH-tafel deze machine is, als er meerdere tafels met een eigen
    # labelprinter zijn (zie config/stations.example.json).
    station_id: str = field(default_factory=lambda: os.environ.get("STATION_ID", ""))

    # Gegevens op de pakbon
    bedrijfsnaam: str = field(
        default_factory=lambda: os.environ.get("BEDRIJF_NAAM", "Things I Like Things I Love")
    )
    bedrijfsadres: str = field(default_factory=lambda: os.environ.get("BEDRIJF_ADRES", ""))
    bedrijfsplaats: str = field(default_factory=lambda: os.environ.get("BEDRIJF_PLAATS", ""))
    bedrijfsemail: str = field(
        default_factory=lambda: os.environ.get("BEDRIJF_EMAIL", "info@thingsilikethingsilove.com")
    )
    bedrijfssite: str = field(
        default_factory=lambda: os.environ.get("BEDRIJF_SITE", "www.thingsilikethingsilove.com")
    )
    btw_nummer: str = field(default_factory=lambda: os.environ.get("BEDRIJF_BTW", ""))


def laad_instellingen() -> Instellingen:
    return Instellingen()
