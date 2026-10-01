"""Switch: show hotspots on the map, editable on the device page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity

from .const import CONF_SHOW_ON_MAP
from .entity import KEY_MAP, FirmsConfigEntity

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
    """Set up switches."""
    del hass
    async_add_entities([ShowOnMapSwitch(entry.runtime_data.coordinator)])


class ShowOnMapSwitch(FirmsConfigEntity, SwitchEntity):
    """Add a geo_location marker per hotspot."""

    _attr_translation_key = "show_on_map"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the switch."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_MAP}")

    @property
    def is_on(self) -> bool:
        """Whether map markers are enabled."""
        return self.coordinator.settings.show_on_map

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable map markers."""
        del kwargs
        self._set_option(CONF_SHOW_ON_MAP, value=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable map markers."""
        del kwargs
        self._set_option(CONF_SHOW_ON_MAP, value=False)
