"""Select: minimum confidence, editable on the device page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity

from .const import CONF_MIN_CONFIDENCE, CONFIDENCE_LEVELS
from .entity import KEY_CONFIDENCE, FirmsConfigEntity

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
    """Set up selects."""
    del hass
    async_add_entities([MinConfidenceSelect(entry.runtime_data.coordinator)])


class MinConfidenceSelect(FirmsConfigEntity, SelectEntity):
    """Ignore detections below the selected confidence level."""

    _attr_translation_key = "min_confidence"
    _attr_options = CONFIDENCE_LEVELS

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the select."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_CONFIDENCE}")

    @property
    def current_option(self) -> str:
        """Current minimum confidence."""
        return self.coordinator.settings.min_confidence

    async def async_select_option(self, option: str) -> None:
        """Store the option and reload the entry."""
        self._set_option(CONF_MIN_CONFIDENCE, option)
