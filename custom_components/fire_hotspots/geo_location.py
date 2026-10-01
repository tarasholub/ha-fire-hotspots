"""Geolocation entities: one map marker per hotspot (optional)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import callback

from .const import (
    ATTR_REGION,
    ATTR_REGION_NAME,
    ATTRIBUTION,
    DOMAIN,
    MARKER_ICON_URL,
    SOURCE_LABELS,
)
from .entity import region_name
from .event import detection_attributes

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .api import Hotspot
    from .coordinator import Detection, FirmsCoordinator
    from .data import FirmsConfigEntry

PARALLEL_UPDATES = 0
HIDDEN_ATTRIBUTES = {"latitude", "longitude", "distance", "source"}
ATTR_SUMMARY = "summary"

_CONFIDENCE_UK = {"low": "низька", "nominal": "номінальна", "high": "висока"}


def hotspot_summary(hotspot: Hotspot, language: str) -> str:
    """One readable line about the detection, for the attributes panel."""
    source = SOURCE_LABELS.get(hotspot.source, hotspot.source)
    parts = [source]
    if hotspot.frp:
        parts.append(
            f"{hotspot.frp:g} МВт" if language == "uk" else f"{hotspot.frp:g} MW"
        )
    if language == "uk":
        confidence = _CONFIDENCE_UK.get(hotspot.confidence, hotspot.confidence)
        parts.append(f"достовірність: {confidence}")
        parts.append("нічна зйомка" if hotspot.daynight == "N" else "денна зйомка")
    else:
        parts.append(f"confidence: {hotspot.confidence}")
        parts.append("night pass" if hotspot.daynight == "N" else "day pass")
    parts.append(f"{hotspot.acquired:%d.%m %H:%M} UTC")
    return " · ".join(parts)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FirmsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up map markers if enabled in options."""
    coordinator = entry.runtime_data.coordinator
    if not coordinator.settings.show_on_map:
        return
    manager = HotspotManager(hass, coordinator, async_add_entities)
    entry.async_on_unload(coordinator.async_add_listener(manager.async_update))
    manager.async_update()


class HotspotManager:
    """
    Add and remove hotspot entities as the feed changes.

    Hotspots are short-lived external events, so entities are created without
    unique IDs and never land in the entity registry.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FirmsCoordinator,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        """Initialize the manager."""
        self._hass = hass
        self._coordinator = coordinator
        self._add = async_add_entities
        self._entities: dict[str, HotspotEntity] = {}

    @callback
    def async_update(self) -> None:
        """Sync entities with the latest coordinator data."""
        current = {d.id: d for d in self._coordinator.data.detections}
        for hotspot_id in list(self._entities):
            if hotspot_id not in current:
                entity = self._entities.pop(hotspot_id)
                self._hass.async_create_task(entity.async_remove(force_remove=True))
        language = self._hass.config.language
        new = [
            HotspotEntity(
                detection,
                region_name(self._coordinator.boundaries, detection.region, language),
                language,
            )
            for hotspot_id, detection in current.items()
            if hotspot_id not in self._entities
        ]
        for entity in new:
            self._entities[entity.hotspot_id] = entity
        if new:
            self._add(new)


class HotspotEntity(GeolocationEvent):
    """A single fire hotspot on the map; state is the distance from home."""

    _attr_should_poll = False
    _attr_source = DOMAIN
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:fire"
    _attr_entity_picture = MARKER_ICON_URL
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS
    # Static per hotspot; keeping them out of the recorder saves database space.
    _unrecorded_attributes = frozenset(
        {
            ATTR_SUMMARY,
            ATTR_REGION,
            ATTR_REGION_NAME,
            "acquired",
            "confidence",
            "frp",
            "satellite",
            "instrument",
            "daynight",
        }
    )

    def __init__(self, detection: Detection, region: str, language: str) -> None:
        """Initialize the entity."""
        hotspot = detection.hotspot
        self.hotspot_id = detection.id
        frp = f", {hotspot.frp:g} MW" if hotspot.frp else ""
        self._attr_name = f"{region}, {hotspot.acquired:%d.%m %H:%M} UTC{frp}"
        self._attr_latitude = hotspot.latitude
        self._attr_longitude = hotspot.longitude
        if detection.distance is not None:
            self._attr_distance = round(detection.distance, 1)
        self._attributes = {
            ATTR_SUMMARY: hotspot_summary(hotspot, language),
            **{
                k: v
                for k, v in detection_attributes(detection).items()
                if k not in HIDDEN_ATTRIBUTES
            },
            ATTR_REGION: detection.region,
            ATTR_REGION_NAME: region,
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Hotspot details."""
        return self._attributes
