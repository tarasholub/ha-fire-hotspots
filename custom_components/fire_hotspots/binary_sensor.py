"""Binary sensors: fire in a region."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .entity import KIND_FIRE, FirmsRegionEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import FirmsCoordinator
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
    async_add_entities(
        FireSensor(coordinator, region) for region in coordinator.settings.regions
    )


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
