"""Numbers: update interval and time window, editable on the device page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfTime

from .const import (
    CONF_HOURS,
    CONF_UPDATE_INTERVAL,
    MAX_HOURS,
    MAX_UPDATE_MINUTES,
    MIN_HOURS,
    MIN_UPDATE_MINUTES,
)
from .entity import KEY_HOURS, KEY_INTERVAL, FirmsConfigEntity

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
    """Set up numbers."""
    del hass
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [UpdateIntervalNumber(coordinator), TimeWindowNumber(coordinator)]
    )


class FirmsOptionNumber(FirmsConfigEntity, NumberEntity):
    """Number that stores an integer config entry option."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _option: str
    _key: str

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the number."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{self._key}")

    async def async_set_native_value(self, value: float) -> None:
        """Store the option and reload the entry."""
        self._set_option(self._option, int(value))


class UpdateIntervalNumber(FirmsOptionNumber):
    """How often to poll FIRMS, in minutes."""

    _attr_translation_key = "update_interval"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = MIN_UPDATE_MINUTES
    _attr_native_max_value = MAX_UPDATE_MINUTES
    _option = CONF_UPDATE_INTERVAL
    _key = KEY_INTERVAL

    @property
    def native_value(self) -> int:
        """Current update interval in minutes."""
        return self.coordinator.settings.update_minutes


class TimeWindowNumber(FirmsOptionNumber):
    """Count detections acquired within this many hours."""

    _attr_translation_key = "time_window"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = MIN_HOURS
    _attr_native_max_value = MAX_HOURS
    _option = CONF_HOURS
    _key = KEY_HOURS

    @property
    def native_value(self) -> int:
        """Current time window in hours."""
        return self.coordinator.settings.hours
