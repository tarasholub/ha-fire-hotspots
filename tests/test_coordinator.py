"""Tests for query planning, processing and the coordinator."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.fire_hotspots.api import (
    FirmsAuthError,
    FirmsClient,
    FirmsRateLimitError,
)
from custom_components.fire_hotspots.boundaries import CountryBoundaries, Region
from custom_components.fire_hotspots.const import WHOLE_COUNTRY
from custom_components.fire_hotspots.coordinator import (
    FirmsCoordinator,
    Settings,
    WatchZone,
    process,
    query_boxes,
    seen_store_key,
)

from .conftest import (
    BUCHA,
    CHISINAU,
    KHERSON,
    KURSK_BORDER,
    KYIV,
    LVIV,
    make_hotspot,
)

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def settings(regions: list[str], **kwargs: object) -> Settings:
    """Build settings with test defaults."""
    options = {"regions": regions, "sources": ["VIIRS_SNPP_NRT"], **kwargs}
    return Settings.from_options(options)


def test_settings_defaults() -> None:
    """Defaults follow SaveEcoBot: all sources, no confidence filter, 24 h."""
    s = Settings.from_options({})
    assert s.sources == [
        "VIIRS_SNPP_NRT",
        "VIIRS_NOAA20_NRT",
        "VIIRS_NOAA21_NRT",
        "MODIS_NRT",
    ]
    assert s.min_confidence == "low"
    assert s.hours == 24
    assert s.day_range == 2
    assert not s.show_on_map
    assert s.update_minutes == 30
    assert s.zones == []
    assert s.zone_radius == 20
    assert Settings.from_options({"hours": 96}).day_range == 5
    assert Settings.from_options({"update_interval": 60.0}).update_minutes == 60


def zone(zone_id: str, lat: float, lon: float, radius: float) -> WatchZone:
    """Build a watch zone for tests."""
    return WatchZone(
        zone_id=zone_id, name=zone_id, latitude=lat, longitude=lon, radius=radius
    )


def test_query_boxes_with_zones(ukraine: CountryBoundaries) -> None:
    """Zone circles extend the query area; zones alone are enough."""
    dacha = zone("zone.dacha", *KURSK_BORDER, 30)
    (box,) = query_boxes(ukraine, settings(["UA-46"]), [dacha])
    assert box[2] > KURSK_BORDER[1]  # stretched east to cover the zone
    (only,) = query_boxes(ukraine, settings([]), [dacha])
    assert only[0] < KURSK_BORDER[1] < only[2]
    assert only[1] < KURSK_BORDER[0] < only[3]


def test_process_zones(ukraine: CountryBoundaries) -> None:
    """Zones count by distance alone, regardless of regions and borders."""
    zones = [zone("zone.home", *KYIV, 50), zone("zone.dacha", *KURSK_BORDER, 30)]
    hotspots = [
        make_hotspot(*KYIV),
        make_hotspot(*BUCHA),
        make_hotspot(*KURSK_BORDER),  # Russia: out of country, inside a zone
        make_hotspot(*KHERSON),  # in country, outside both zones
    ]
    s = settings([WHOLE_COUNTRY])
    now = dt_util.utcnow()
    data = process(hotspots, ukraine, s, home=None, now=now, seen=None, zones=zones)
    home_zone = data.zones["zone.home"]
    assert home_zone.count == 2  # Kyiv + Bucha
    assert home_zone.nearest == pytest.approx(0, abs=0.5)
    distances = [d.distance for d in home_zone.detections]
    assert distances == sorted(distances)
    assert data.zones["zone.dacha"].count == 1
    assert data.counts[WHOLE_COUNTRY] == 3  # border hotspot not in the country
    assert home_zone.new == []  # first refresh is a baseline

    seen = {d.id for d in data.detections} | {
        d.id for zone_data in data.zones.values() for d in zone_data.detections
    }
    fresh = [*hotspots, make_hotspot(*BUCHA, hours_ago=0.5)]
    data = process(fresh, ukraine, s, home=None, now=now, seen=seen, zones=zones)
    assert len(data.zones["zone.home"].new) == 1
    assert data.zones["zone.dacha"].new == []


def test_query_boxes(ukraine: CountryBoundaries) -> None:
    """Selected regions collapse into one union box."""
    (box,) = query_boxes(ukraine, settings(["UA-30", "UA-46"]))
    west, south, east, north = box
    assert west < LVIV[1]
    assert east > KYIV[1]
    assert south < LVIV[0]
    assert north > KYIV[0]
    (country,) = query_boxes(ukraine, settings([WHOLE_COUNTRY]))
    assert country[0] < 23
    assert country[2] > 40
    assert query_boxes(ukraine, settings([])) == []


def test_query_boxes_antimeridian() -> None:
    """Regions on both sides of 180° get separate boxes."""
    square = [[[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]]
    boundaries = CountryBoundaries(
        country="FJI",
        level="ADM1",
        regions={
            "E": Region("E", {"en": "E"}, [(177, -18, 180, -16)], square),
            "W": Region("W", {"en": "W"}, [(-180, -17, -179, -16)], square),
        },
    )
    assert query_boxes(boundaries, settings([WHOLE_COUNTRY])) == [
        (177, -18, 180, -16),
        (-180, -17, -179, -16),
    ]


def test_process_counts_and_filters(ukraine: CountryBoundaries) -> None:
    """Counts per region and country; foreign, old and low-confidence dropped."""
    hotspots = [
        make_hotspot(*KYIV),
        make_hotspot(*KYIV, source="MODIS_NRT"),  # same fire, other satellite
        make_hotspot(*KHERSON),
        make_hotspot(*KHERSON),  # exact duplicate (same id)
        make_hotspot(*BUCHA),
        make_hotspot(*LVIV, hours_ago=30),  # outside window
        make_hotspot(*KURSK_BORDER),  # Russia
        make_hotspot(*CHISINAU),  # Moldova
        make_hotspot(*LVIV, confidence="low"),
    ]
    s = settings(["UA-30", "UA-65", WHOLE_COUNTRY], min_confidence="low")
    data = process(hotspots, ukraine, s, home=KYIV, now=dt_util.utcnow(), seen=None)
    assert data.counts == {"UA-30": 2, "UA-65": 1, WHOLE_COUNTRY: 5}
    assert len(data.detections) == 5
    assert data.new == {}  # first refresh is a baseline
    kyiv = next(d for d in data.detections if d.region == "UA-30")
    assert kyiv.distance == pytest.approx(0, abs=0.1)

    strict = settings(["UA-46"], min_confidence="nominal")
    data = process(
        hotspots, ukraine, strict, home=None, now=dt_util.utcnow(), seen=None
    )
    assert data.counts == {"UA-46": 0}
    assert data.detections == []


def test_process_new_grouped_by_region(ukraine: CountryBoundaries) -> None:
    """New detections are grouped by their actual region."""
    old = make_hotspot(*KYIV)
    s = settings([WHOLE_COUNTRY])
    first = process([old], ukraine, s, home=None, now=dt_util.utcnow(), seen=None)
    seen = {d.id for d in first.detections}
    fresh = [
        old,
        make_hotspot(*KHERSON),
        make_hotspot(*BUCHA),
        make_hotspot(*BUCHA, hours_ago=2),
    ]
    data = process(fresh, ukraine, s, home=None, now=dt_util.utcnow(), seen=seen)
    assert {k: len(v) for k, v in data.new.items()} == {"UA-65": 1, "UA-32": 2}


async def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, ukraine: CountryBoundaries
) -> FirmsCoordinator:
    entry.add_to_hass(hass)
    client = FirmsClient(async_get_clientsession(hass), "key")
    return FirmsCoordinator(hass, entry, client, ukraine)


async def test_coordinator_refresh(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    ukraine: CountryBoundaries,
    mock_hotspots: AsyncMock,
) -> None:
    """One request per source and box; second refresh reports new ones."""
    coordinator = await _coordinator(hass, config_entry, ukraine)
    mock_hotspots.return_value = [make_hotspot(*KYIV)]
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.counts["UA-30"] == 1
    assert coordinator.data.new == {}
    assert mock_hotspots.call_count == 1
    source, box, day_range = mock_hotspots.call_args.args
    assert source == "VIIRS_SNPP_NRT"
    assert day_range == 2
    assert box[0] < 23

    mock_hotspots.return_value = [make_hotspot(*KYIV), make_hotspot(*KHERSON)]
    await coordinator.async_refresh()
    assert list(coordinator.data.new) == ["UA-65"]


async def test_coordinator_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    ukraine: CountryBoundaries,
    mock_hotspots: AsyncMock,
) -> None:
    """Auth errors trigger reauth, others mark the update failed."""
    coordinator = await _coordinator(hass, config_entry, ukraine)
    mock_hotspots.side_effect = FirmsRateLimitError
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    mock_hotspots.side_effect = FirmsAuthError
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_seen_restored_after_restart(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    ukraine: CountryBoundaries,
    mock_hotspots: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """Detections from before a restart are not new; missed ones are."""
    coordinator = await _coordinator(hass, config_entry, ukraine)
    kyiv = make_hotspot(*KYIV)
    hass_storage[seen_store_key(config_entry.entry_id)] = {
        "version": 1,
        "data": {"fingerprint": coordinator._fingerprint, "seen": [kyiv.id]},
    }
    await coordinator._async_setup()
    mock_hotspots.return_value = [kyiv, make_hotspot(*KHERSON)]
    await coordinator.async_refresh()
    assert list(coordinator.data.new) == ["UA-65"]


async def test_seen_ignored_when_scope_changed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    ukraine: CountryBoundaries,
    mock_hotspots: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """A different scope starts a fresh baseline."""
    coordinator = await _coordinator(hass, config_entry, ukraine)
    hass_storage[seen_store_key(config_entry.entry_id)] = {
        "version": 1,
        "data": {"fingerprint": "other", "seen": []},
    }
    await coordinator._async_setup()
    mock_hotspots.return_value = [make_hotspot(*KHERSON)]
    await coordinator.async_refresh()
    assert coordinator.data.new == {}


async def test_seen_saved(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    ukraine: CountryBoundaries,
    mock_hotspots: AsyncMock,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Seen ids and the scope fingerprint are persisted after a refresh."""
    coordinator = await _coordinator(hass, config_entry, ukraine)
    kyiv = make_hotspot(*KYIV)
    mock_hotspots.return_value = [kyiv]
    await coordinator.async_refresh()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    stored = hass_storage[seen_store_key(config_entry.entry_id)]["data"]
    assert stored == {"fingerprint": coordinator._fingerprint, "seen": [kyiv.id]}
