"""SRS-koppeling: kies de backend op basis van de instellingen."""

from __future__ import annotations

from ..config import Instellingen
from .base import PhNietGevonden, SrsClient, SrsFout
from .mock import MockSrsClient
from .rest import RestSrsClient

__all__ = [
    "MockSrsClient",
    "PhNietGevonden",
    "RestSrsClient",
    "SrsClient",
    "SrsFout",
    "maak_client",
]


def maak_client(instellingen: Instellingen) -> SrsClient:
    backend = instellingen.srs_backend.lower()
    if backend == "mock":
        return MockSrsClient(instellingen.fixture_bestand)
    if backend == "rest":
        return RestSrsClient(instellingen)
    raise SrsFout(f"Onbekende SRS_BACKEND '{instellingen.srs_backend}' (kies mock of rest).")
