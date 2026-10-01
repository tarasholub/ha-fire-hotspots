"""Diagnostics for Fire Hotspots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_MAP_KEY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import FirmsConfigEntry

TO_REDACT = {CONF_MAP_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FirmsConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without the MAP_KEY or home distances."""
    del hass
    coordinator = entry.runtime_data.coordinator
    boundaries = coordinator.boundaries
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "boundaries": {
            "country": boundaries.country,
            "level": boundaries.level,
            "regions": len(boundaries.regions),
        },
        "last_update_success": coordinator.last_update_success,
        "counts": coordinator.data.counts,
        "detections": len(coordinator.data.detections),
    }
