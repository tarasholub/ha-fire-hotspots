"""Sensors: hotspot counts per region and distance to the nearest hotspot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfLength

from .const import ATTR_ACQUIRED, ATTR_REGION
from .entity import KEY_NEAREST, KIND_COUNT, FirmsEntity, FirmsRegionEntity

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
    """Set up sensors."""
    del hass
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [NearestHotspotSensor(coordinator)]
    entities.extend(
        HotspotCountSensor(coordinator, region)
        for region in coordinator.settings.regions
    )
    async_add_entities(entities)


class HotspotCountSensor(FirmsRegionEntity, SensorEntity):
    """Hotspots in a region within the time window."""

    _region_translation_key = "hotspots"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FirmsCoordinator, region: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, KIND_COUNT, region)

    @property
    def native_value(self) -> int:
        """Number of hotspots."""
        return self.coordinator.data.counts.get(self.region, 0)


class NearestHotspotSensor(FirmsEntity, SensorEntity):
    """Distance from Home Assistant home to the nearest monitored hotspot."""

    _attr_translation_key = "nearest_hotspot"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the sensor."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_NEAREST}")

    def _nearest(self) -> Detection | None:
        located = [
            d for d in self.coordinator.data.detections if d.distance is not None
        ]
        return min(located, key=lambda d: d.distance) if located else None

    @property
    def native_value(self) -> float | None:
        """Distance in km; unknown without hotspots or a home location."""
        nearest = self._nearest()
        return round(nearest.distance, 2) if nearest else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Region and acquisition time of the nearest hotspot."""
        nearest = self._nearest()
        if nearest is None:
            return {}
        return {
            ATTR_REGION: nearest.region,
            ATTR_ACQUIRED: nearest.hotspot.acquired.isoformat(),
        }
