from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASIS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASIS))

from app.config import Instellingen  # noqa: E402
from app.service import PhService  # noqa: E402
from app.srs.mock import MockSrsClient  # noqa: E402
from app.store import Opslag  # noqa: E402


@pytest.fixture
def instellingen(tmp_path: Path) -> Instellingen:
    return Instellingen(
        srs_backend="mock",
        fixture_bestand=BASIS / "fixtures" / "srs_demo.json",
        database=tmp_path / "controles.db",
        hal_bestand=tmp_path / "hal-bestaat-niet.json",
        letop_uren=4,
        vastloper_uren=24,
        stilstand_uren=8,
        snelmodus=True,
        auto_start=True,
    )


@pytest.fixture
def srs(instellingen: Instellingen) -> MockSrsClient:
    return MockSrsClient(instellingen.fixture_bestand)


@pytest.fixture
def opslag(instellingen: Instellingen) -> Opslag:
    winkel = Opslag(instellingen.database)
    yield winkel
    winkel.close()


@pytest.fixture
def service(srs: MockSrsClient, opslag: Opslag, instellingen: Instellingen) -> PhService:
    return PhService(srs, opslag, instellingen)
