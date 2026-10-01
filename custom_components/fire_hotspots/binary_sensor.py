"""Binary sensors: fire in a region, fire near a zone, stale data."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .const import STALE_AFTER_HOURS
from .entity import (
    KEY_STALE,
    KIND_FIRE,
    KIND_ZONE_FIRE,
    FirmsEntity,
    FirmsRegionEntity,
    FirmsZoneEntity,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import FirmsCoordinator, WatchZone
    from .data import FirmsConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FirmsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    del hass
    coordinator = entry.runtime_data.coordinator
    entities: list[BinarySensorEntity] = [StaleDataSensor(coordinator)]
    entities.extend(
        FireSensor(coordinator, region) for region in coordinator.settings.regions
    )
    entities.extend(
        ZoneFireSensor(coordinator, zone) for zone in coordinator.watch_zones()
    )
    async_add_entities(entities)


class FireSensor(FirmsRegionEntity, BinarySensorEntity):
    """On (unsafe) while a region has hotspots within the time window."""

    _region_translation_key = "fire"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: FirmsCoordinator, region: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, KIND_FIRE, region)

    @property
    def is_on(self) -> bool:
        """Return True if the region has hotspots."""
        return self.coordinator.data.counts.get(self.region, 0) > 0


class ZoneFireSensor(FirmsZoneEntity, BinarySensorEntity):
    """On (unsafe) while a watched zone has hotspots within its radius."""

    _zone_translation_key = "zone_fire"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: FirmsCoordinator, zone: WatchZone) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, KIND_ZONE_FIRE, zone)

    @property
    def is_on(self) -> bool:
        """Return True if the zone's radius has hotspots."""
        data = self.coordinator.data.zones.get(self.zone_id)
        return data.count > 0 if data else False


class StaleDataSensor(FirmsEntity, BinarySensorEntity):
    """Problem: the newest raw detection is older than expected."""

    _attr_translation_key = "stale_data"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the binary sensor."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_STALE}")

    @property
    def is_on(self) -> bool | None:
        """
        True when no source delivered anything recent.

        Unknown while the raw feed is completely empty: an empty area is
        indistinguishable from missing data.
        """
        acquired = [
            latest
            for latest in self.coordinator.data.latest_by_source.values()
            if latest is not None
        ]
        if not acquired:
            return None
        return max(acquired) < dt_util.utcnow() - timedelta(hours=STALE_AFTER_HOURS)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Newest raw acquisition time per satellite source."""
        return {
            source: latest.isoformat() if latest else None
            for source, latest in self.coordinator.data.latest_by_source.items()
        }
