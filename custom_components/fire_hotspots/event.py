"""Event entity: new hotspots, one event per region per update."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventEntity
from homeassistant.core import callback

from .const import (
    ATTR_ACQUIRED,
    ATTR_CONFIDENCE,
    ATTR_COUNT,
    ATTR_DAYNIGHT,
    ATTR_DETECTIONS,
    ATTR_DISTANCE,
    ATTR_FRP,
    ATTR_INSTRUMENT,
    ATTR_REGION,
    ATTR_REGION_NAME,
    ATTR_SATELLITE,
    ATTR_SOURCE,
    EVENT_DETECTED,
    MAX_EVENT_DETECTIONS,
)
from .entity import KEY_NEW, FirmsEntity, region_name

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import Detection, FirmsCoordinator
    from .data import FirmsConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FirmsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event entity."""
    del hass
    async_add_entities([NewHotspotsEvent(entry.runtime_data.coordinator)])


def detection_attributes(detection: Detection) -> dict[str, Any]:
    """Compact description of one detection."""
    hotspot = detection.hotspot
    return {
        "latitude": hotspot.latitude,
        "longitude": hotspot.longitude,
        ATTR_ACQUIRED: hotspot.acquired.isoformat(),
        ATTR_DISTANCE: (
            round(detection.distance, 2) if detection.distance is not None else None
        ),
        ATTR_CONFIDENCE: hotspot.confidence,
        ATTR_FRP: hotspot.frp,
        ATTR_SATELLITE: hotspot.satellite,
        ATTR_INSTRUMENT: hotspot.instrument,
        ATTR_DAYNIGHT: hotspot.daynight,
        ATTR_SOURCE: hotspot.source,
    }


class NewHotspotsEvent(FirmsEntity, EventEntity):
    """Fires `detected` once per region that has new hotspots."""

    _attr_translation_key = "new_hotspots"
    _attr_event_types = [EVENT_DETECTED]  # noqa: RUF012

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the event entity."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_NEW}")

    @callback
    def _handle_coordinator_update(self) -> None:
        language = self.hass.config.language
        for region, detections in self.coordinator.data.new.items():
            nearest_first = sorted(
                detections,
                key=lambda d: d.distance if d.distance is not None else 0,
            )
            self._trigger_event(
                EVENT_DETECTED,
                {
                    ATTR_REGION: region,
                    ATTR_REGION_NAME: region_name(
                        self.coordinator.boundaries, region, language
                    ),
                    ATTR_COUNT: len(detections),
                    ATTR_DETECTIONS: [
                        detection_attributes(d)
                        for d in nearest_first[:MAX_EVENT_DETECTIONS]
                    ],
                },
            )
            self.async_write_ha_state()
