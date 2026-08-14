"""Packing tables.

With one table the printer is a single setting. With sixteen, the app runs
centrally and each table has its own machine driving its own USB label printer
over CUPS, so a label has to be routed to the right one.

A station is defined in a JSON file, e.g.:

    [
      {"id": "tafel-01", "name": "Tafel 1", "cups_host": "tafel-01.local",
       "cups_printer": "zd220"},
      {"id": "tafel-02", "name": "Tafel 2", "cups_host": "tafel-02.local",
       "cups_printer": "zd220"}
    ]

The browser remembers which station it is, so a packer picks it once.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from .config import VerzendInstellingen as Settings


class Station(BaseModel):
    id: str
    name: str
    # The machine at the table running CUPS with the label printer attached.
    cups_host: str
    cups_printer: str


class StationError(Exception):
    pass


def load_stations(settings: Settings) -> list[Station]:
    """Read the configured tables. Empty when running as a single station."""
    path = settings.stations_file
    if not path:
        return []
    if not path.exists():
        raise StationError(f"STATIONS_FILE {path} bestaat niet.")
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise StationError(f"Kan {path} niet lezen: {exc}") from exc
    return [Station(**entry) for entry in entries]


def resolve_station(settings: Settings, station_id: str | None) -> Station | None:
    """Find the station a request came from.

    Returns None when no stations are configured at all — then the app is
    serving a single table and falls back to the printer in .env.
    """
    stations = load_stations(settings)
    if not stations:
        return None
    if not station_id:
        raise StationError(
            "Deze werkplek heeft nog geen tafel gekozen. Kies een tafel op het scherm."
        )
    for station in stations:
        if station.id == station_id:
            return station
    raise StationError(f"Onbekende tafel '{station_id}'.")
