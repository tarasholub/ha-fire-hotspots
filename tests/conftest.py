"""Shared fixtures for Fire Hotspots tests."""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fire_hotspots.api import Hotspot, KeyStatus
from custom_components.fire_hotspots.boundaries import CountryBoundaries, load_cached
from custom_components.fire_hotspots.const import (
    CONF_COUNTRY,
    CONF_HOURS,
    CONF_MAP_KEY,
    CONF_MIN_CONFIDENCE,
    CONF_REGIONS,
    CONF_SHOW_ON_MAP,
    CONF_SOURCES,
    DOMAIN,
    SOURCE_VIIRS_SNPP,
    WHOLE_COUNTRY,
)

if TYPE_CHECKING:
    from collections.abc import Generator

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES = Path(__file__).parent / "fixtures"

# Points inside Ukrainian regions
KYIV = (50.45, 30.52)  # UA-30
BUCHA = (50.54, 30.21)  # UA-32
KHERSON = (46.64, 32.61)  # UA-65
LVIV = (49.84, 24.03)  # UA-46
KURSK_BORDER = (51.07, 35.35)  # Russia, just across the border
CHISINAU = (47.01, 28.86)  # Moldova


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in every test."""
    return


@pytest.fixture
def ukraine() -> CountryBoundaries:
    """Real simplified Ukrainian boundaries."""
    boundaries = load_cached(FIXTURES, "UKR")
    assert boundaries is not None
    return boundaries


@pytest.fixture
def boundaries_cache(tmp_path: Path) -> Generator[Path]:
    """Point the integration cache at a temp dir pre-filled with Ukraine."""
    shutil.copy(FIXTURES / "UKR.json", tmp_path / "UKR.json")
    with patch(
        "custom_components.fire_hotspots.helpers.cache_dir", return_value=tmp_path
    ):
        yield tmp_path


def make_hotspot(
    lat: float,
    lon: float,
    *,
    hours_ago: float = 1,
    confidence: str = "nominal",
    source: str = SOURCE_VIIRS_SNPP,
) -> Hotspot:
    """Build a hotspot acquired hours_ago."""
    acquired = (dt_util.utcnow() - timedelta(hours=hours_ago)).replace(
        second=0, microsecond=0
    )
    return Hotspot(
        source=source,
        latitude=lat,
        longitude=lon,
        acquired=acquired,
        satellite="N",
        instrument="VIIRS",
        confidence=confidence,
        frp=4.2,
        daynight="N",
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Ukraine entry: Kyiv city, Kherson region and the whole country."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ukraine",
        unique_id="UA",
        data={CONF_MAP_KEY: "test-key", CONF_COUNTRY: "UA"},
        options={
            CONF_REGIONS: ["UA-30", "UA-65", WHOLE_COUNTRY],
            CONF_HOURS: 24,
            CONF_MIN_CONFIDENCE: "low",
            CONF_SOURCES: [SOURCE_VIIRS_SNPP],
            CONF_SHOW_ON_MAP: False,
        },
    )


@pytest.fixture
def mock_hotspots() -> Generator[AsyncMock]:
    """Patch FIRMS fetching; set .return_value to a list of hotspots."""
    with (
        patch(
            "custom_components.fire_hotspots.api.FirmsClient.async_get_hotspots",
            new_callable=AsyncMock,
        ) as mock,
        patch(
            "custom_components.fire_hotspots.api.FirmsClient.async_get_key_status",
            new_callable=AsyncMock,
            return_value=KeyStatus(used=16, limit=5000, interval="10 minutes"),
        ),
    ):
        mock.return_value = []
        yield mock
