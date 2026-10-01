"""Fire Hotspots integration for Home Assistant (data: NASA FIRMS)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import FirmsClient
from .boundaries import BoundariesError
from .const import CONF_COUNTRY, CONF_MAP_KEY, DOMAIN
from .coordinator import (
    STORAGE_VERSION,
    FirmsCoordinator,
    rate_limit_issue_id,
    seen_store_key,
)
from .data import FirmsRuntimeData
from .entity import expected_unique_ids
from .helpers import async_get_boundaries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import FirmsConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.GEO_LOCATION,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: FirmsConfigEntry) -> bool:
    """Set up Fire Hotspots from a config entry."""
    try:
        boundaries = await async_get_boundaries(hass, entry.data[CONF_COUNTRY])
    except BoundariesError as err:
        msg = f"Boundaries unavailable: {err}"
        raise ConfigEntryNotReady(msg) from err

    client = FirmsClient(async_get_clientsession(hass), entry.data[CONF_MAP_KEY])
    coordinator = FirmsCoordinator(hass, entry, client, boundaries)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = FirmsRuntimeData(coordinator=coordinator)
    _remove_stale_entities(hass, entry, coordinator.settings.regions)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_stale_entities(
    hass: HomeAssistant, entry: FirmsConfigEntry, regions: list[str]
) -> None:
    """Drop entities of regions that were deselected in options."""
    registry = er.async_get(hass)
    expected = expected_unique_ids(entry.entry_id, regions)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in expected:
            registry.async_remove(entity.entity_id)


async def async_remove_entry(hass: HomeAssistant, entry: FirmsConfigEntry) -> None:
    """Remove persisted detections and open issues of a deleted entry."""
    ir.async_delete_issue(hass, DOMAIN, rate_limit_issue_id(entry.entry_id))
    await Store(hass, STORAGE_VERSION, seen_store_key(entry.entry_id)).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: FirmsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
