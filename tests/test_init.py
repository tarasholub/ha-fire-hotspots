"""End-to-end tests: setup, entities, events, map, cleanup and unload."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.fire_hotspots.api import (
    FirmsAuthError,
    FirmsConnectionError,
    FirmsRateLimitError,
)
from custom_components.fire_hotspots.boundaries import BoundariesError
from custom_components.fire_hotspots.const import (
    CONF_MIN_CONFIDENCE,
    CONF_REGIONS,
    CONF_SHOW_ON_MAP,
    CONF_UPDATE_INTERVAL,
    CONF_ZONE_RADIUS,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.fire_hotspots.coordinator import (
    rate_limit_issue_id,
    seen_store_key,
)
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
    assert config_entry.runtime_data.coordinator.update_interval == timedelta(
        minutes=30
    )

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

    kyiv = hass.states.get(_entity_id(hass, "count_ua-30"))
    assert kyiv.attributes["frp_sum"] == 8.4  # two hotspots, 4.2 MW each
    assert kyiv.attributes["latest_acquired"]
    kherson = hass.states.get(_entity_id(hass, "count_ua-65"))
    assert kherson.attributes["frp_sum"] == 0
    assert kherson.attributes["latest_acquired"] is None
    assert float(hass.states.get(_entity_id(hass, "frp")).state) == 12.6
    last = hass.states.get(_entity_id(hass, "last"))
    assert last.state == kyiv.attributes["latest_acquired"]

    usage = hass.states.get(_entity_id(hass, "usage"))
    assert usage.state == "16"
    assert usage.attributes["limit"] == 5000
    assert usage.attributes["interval"] == "10 minutes"


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
    assert kherson.attributes["entity_picture"] == "/fire_hotspots/icon.png"
    assert kherson.attributes["friendly_name"].endswith("4.2 MW")
    assert kherson.attributes["summary"].startswith(
        "VIIRS S-NPP · 4.2 MW · confidence: nominal · night pass"
    )

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


async def test_config_entities_update_options(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Config entities on the device write options and reload the entry."""
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _entity_id(hass, "interval"), "value": 60},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert config_entry.options[CONF_UPDATE_INTERVAL] == 60
    assert config_entry.runtime_data.coordinator.update_interval == timedelta(
        minutes=60
    )

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _entity_id(hass, "confidence"), "option": "high"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert config_entry.options[CONF_MIN_CONFIDENCE] == "high"
    assert hass.states.get(_entity_id(hass, "confidence")).state == "high"

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": _entity_id(hass, "map")},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert config_entry.options[CONF_SHOW_ON_MAP] is True
    assert hass.states.get(_entity_id(hass, "map")).state == "on"


async def test_zone_entities_and_events(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Watched zones count across the border and fire their own event."""
    hass.states.async_set(
        "zone.dacha",
        "0",
        {
            "latitude": KURSK_BORDER[0],
            "longitude": KURSK_BORDER[1],
            "radius": 100,
            "friendly_name": "Dacha",
        },
    )
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            **config_entry.options,
            CONF_ZONES: ["zone.dacha"],
            CONF_ZONE_RADIUS: 30,
        },
    )
    mock_hotspots.return_value = [make_hotspot(*KURSK_BORDER)]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    count = hass.states.get(_entity_id(hass, "zone_count_dacha"))
    assert count.state == "1"
    assert "Dacha" in count.attributes["friendly_name"]
    assert count.attributes["frp_sum"] == 4.2
    assert hass.states.get(_entity_id(hass, "zone_fire_dacha")).state == "on"
    assert float(hass.states.get(_entity_id(hass, "zone_nearest_dacha")).state) < 1
    # the border hotspot is outside the country, so regions stay clear
    assert hass.states.get(_entity_id(hass, "count_country")).state == "0"

    mock_hotspots.return_value = [
        make_hotspot(*KURSK_BORDER),
        make_hotspot(*KURSK_BORDER, hours_ago=0.5),
    ]
    await _refresh(hass, config_entry)
    event = hass.states.get(_entity_id(hass, "new"))
    assert event.attributes["event_type"] == "detected_near_zone"
    assert event.attributes["zone"] == "zone.dacha"
    assert event.attributes["zone_name"] == "Dacha"
    assert event.attributes["count"] == 1
    assert event.attributes["detections"][0]["distance"] < 1


async def test_stale_data_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Problem sensor flags an aging raw feed; empty feed is unknown."""
    mock_hotspots.return_value = [make_hotspot(*KYIV, hours_ago=10)]
    await _setup(hass, config_entry)
    stale = hass.states.get(_entity_id(hass, "stale"))
    assert stale.state == "on"
    assert stale.attributes["VIIRS_SNPP_NRT"] is not None

    mock_hotspots.return_value = [make_hotspot(*KYIV)]
    await _refresh(hass, config_entry)
    assert hass.states.get(_entity_id(hass, "stale")).state == "off"

    mock_hotspots.return_value = []
    await _refresh(hass, config_entry)
    assert hass.states.get(_entity_id(hass, "stale")).state == "unknown"


async def test_rate_limit_opens_and_closes_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_hotspots: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """A rate-limited refresh opens a repairs issue; recovery closes it."""
    await _setup(hass, config_entry)
    registry = ir.async_get(hass)
    issue_id = rate_limit_issue_id(config_entry.entry_id)
    assert registry.async_get_issue(DOMAIN, issue_id) is None

    mock_hotspots.side_effect = FirmsRateLimitError("limit")
    await _refresh(hass, config_entry)
    assert hass.states.get(_entity_id(hass, "count_ua-30")).state == "unavailable"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_hotspots.side_effect = None
    await _refresh(hass, config_entry)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


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
