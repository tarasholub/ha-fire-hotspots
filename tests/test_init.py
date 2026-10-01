"""End-to-end tests: setup, entities, events, map, cleanup and unload."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.fire_hotspots.api import FirmsAuthError, FirmsConnectionError
from custom_components.fire_hotspots.boundaries import BoundariesError
from custom_components.fire_hotspots.const import CONF_REGIONS, CONF_SHOW_ON_MAP
from custom_components.fire_hotspots.coordinator import seen_store_key
from custom_components.fire_hotspots.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import BUCHA, KHERSON, KURSK_BORDER, KYIV, LVIV, make_hotspot

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import AsyncMock

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entity_id(hass: HomeAssistant, suffix: str) -> str:
    registry = er.async_get(hass)
    return next(
        e.entity_id
        for e in registry.entities.values()
        if e.unique_id.endswith(f"_{suffix}")
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Counts, fire sensors and nearest distance for selected regions."""
    hass.config.latitude, hass.config.longitude = BUCHA
    mock_hotspots.return_value = [
        make_hotspot(*KYIV),
        make_hotspot(*KYIV, source="MODIS_NRT"),
        make_hotspot(*BUCHA),
        make_hotspot(*KURSK_BORDER),
    ]
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    assert hass.states.get(_entity_id(hass, "count_ua-30")).state == "2"
    assert hass.states.get(_entity_id(hass, "count_ua-65")).state == "0"
    assert hass.states.get(_entity_id(hass, "count_country")).state == "3"
    assert hass.states.get(_entity_id(hass, "fire_ua-30")).state == "on"
    assert hass.states.get(_entity_id(hass, "fire_ua-65")).state == "off"
    assert hass.states.get(_entity_id(hass, "fire_country")).state == "on"
    nearest = hass.states.get(_entity_id(hass, "nearest"))
    assert float(nearest.state) == 0
    assert nearest.attributes["region"] == "UA-32"
    assert hass.states.get(_entity_id(hass, "new")).state == "unknown"
    assert hass.states.async_all("geo_location") == []  # map disabled


async def test_entity_names(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Names use the region in the HA language."""
    hass.config.language = "uk"
    await _setup(hass, config_entry)
    state = hass.states.get(_entity_id(hass, "count_ua-65"))
    assert state.attributes["friendly_name"].endswith("Херсонська область")


async def test_new_hotspots_event(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """One event per region with new hotspots, nearest first."""
    hass.config.latitude, hass.config.longitude = KYIV
    mock_hotspots.return_value = [make_hotspot(*KYIV)]
    await _setup(hass, config_entry)

    mock_hotspots.return_value = [
        make_hotspot(*KYIV),
        make_hotspot(*KHERSON),
        make_hotspot(46.70, 32.70),
    ]
    await _refresh(hass, config_entry)
    state = hass.states.get(_entity_id(hass, "new"))
    assert state.attributes["event_type"] == "detected"
    assert state.attributes["region"] == "UA-65"
    assert state.attributes["count"] == 2
    detections = state.attributes["detections"]
    assert detections[0]["distance"] <= detections[1]["distance"]
    assert detections[0]["latitude"] == 46.70  # north of Kherson: closer to Kyiv


async def test_map_markers(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Markers follow detections when the map is enabled."""
    hass.config.latitude, hass.config.longitude = KYIV
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_SHOW_ON_MAP: True}
    )
    mock_hotspots.return_value = [make_hotspot(*KYIV), make_hotspot(*KHERSON)]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    markers = hass.states.async_all("geo_location")
    assert len(markers) == 2
    kherson = next(s for s in markers if s.attributes["region"] == "UA-65")
    assert float(kherson.state) > 400
    assert kherson.attributes["source"] == "fire_hotspots"

    mock_hotspots.return_value = [make_hotspot(*KYIV)]
    await _refresh(hass, config_entry)
    assert len(hass.states.async_all("geo_location")) == 1

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_all("geo_location") == []


async def test_deselected_region_entities_removed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Changing regions in options removes stale entities."""
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id(
        "sensor", "fire_hotspots", f"{config_entry.entry_id}_count_ua-65"
    )
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_REGIONS: ["UA-46"]}
    )
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not registry.async_get_entity_id(
        "sensor", "fire_hotspots", f"{config_entry.entry_id}_count_ua-65"
    )
    assert registry.async_get_entity_id(
        "sensor", "fire_hotspots", f"{config_entry.entry_id}_count_ua-46"
    )
    mock_hotspots.return_value = [make_hotspot(*LVIV)]
    await _refresh(hass, config_entry)
    assert hass.states.get(_entity_id(hass, "fire_ua-46")).state == "on"


async def test_setup_retry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """FIRMS connection errors retry setup."""
    mock_hotspots.side_effect = FirmsConnectionError
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retry_without_boundaries(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_hotspots: AsyncMock
) -> None:
    """Missing boundaries retry setup."""
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.fire_hotspots.async_get_boundaries",
        side_effect=BoundariesError,
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_error_starts_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Invalid key triggers reauth."""
    mock_hotspots.side_effect = FirmsAuthError
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_remove_entry_deletes_store(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
    hass_storage: dict[str, Any],
) -> None:
    """Removing the entry removes persisted detections."""
    await _setup(hass, config_entry)
    key = seen_store_key(config_entry.entry_id)
    hass_storage[key] = {"version": 1, "data": {"fingerprint": "", "seen": []}}
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_diagnostics(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """MAP_KEY is redacted; counts and boundaries are included."""
    mock_hotspots.return_value = [make_hotspot(*KYIV)]
    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diag["entry"]["data"]["map_key"] == "**REDACTED**"
    assert "test-key" not in str(diag)
    assert diag["boundaries"] == {"country": "UKR", "level": "ADM1", "regions": 27}
    assert diag["counts"]["UA-30"] == 1
