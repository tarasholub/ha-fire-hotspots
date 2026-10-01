"""Base entity and unique id helpers for Fire Hotspots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, FIRMS_MAP_URL, WHOLE_COUNTRY
from .coordinator import FirmsCoordinator, WatchZone

if TYPE_CHECKING:
    from .boundaries import CountryBoundaries

KIND_COUNT = "count"
KIND_FIRE = "fire"
KIND_ZONE_COUNT = "zone_count"
KIND_ZONE_FIRE = "zone_fire"
KIND_ZONE_NEAREST = "zone_nearest"
ZONE_KINDS = (KIND_ZONE_COUNT, KIND_ZONE_FIRE, KIND_ZONE_NEAREST)
KEY_NEAREST = "nearest"
KEY_NEW = "new"
KEY_LAST = "last"
KEY_FRP = "frp"
KEY_INTERVAL = "interval"
KEY_HOURS = "hours"
KEY_CONFIDENCE = "confidence"
KEY_MAP = "map"
KEY_STALE = "stale"
KEY_USAGE = "usage"


def region_unique_id(entry_id: str, kind: str, region: str) -> str:
    """Build the unique id of a per-region entity."""
    return f"{entry_id}_{kind}_{region.lower()}"


def zone_unique_id(entry_id: str, kind: str, zone_id: str) -> str:
    """Build the unique id of a per-zone entity from the zone entity id."""
    return f"{entry_id}_{kind}_{zone_id.partition('.')[2]}"


def expected_unique_ids(
    entry_id: str, regions: list[str], zones: list[str]
) -> set[str]:
    """Return all unique ids an entry should have for its regions and zones."""
    ids = {
        f"{entry_id}_{KEY_NEAREST}",
        f"{entry_id}_{KEY_NEW}",
        f"{entry_id}_{KEY_LAST}",
        f"{entry_id}_{KEY_FRP}",
        f"{entry_id}_{KEY_INTERVAL}",
        f"{entry_id}_{KEY_HOURS}",
        f"{entry_id}_{KEY_CONFIDENCE}",
        f"{entry_id}_{KEY_MAP}",
        f"{entry_id}_{KEY_STALE}",
        f"{entry_id}_{KEY_USAGE}",
    }
    ids.update(
        region_unique_id(entry_id, kind, region)
        for region in regions
        for kind in (KIND_COUNT, KIND_FIRE)
    )
    ids.update(
        zone_unique_id(entry_id, kind, zone) for zone in zones for kind in ZONE_KINDS
    )
    return ids


def region_name(boundaries: CountryBoundaries, region: str, language: str) -> str:
    """Return the localized region name; empty for the whole country."""
    if region == WHOLE_COUNTRY or region not in boundaries.regions:
        return ""
    return boundaries.regions[region].name(language)


class FirmsEntity(CoordinatorEntity[FirmsCoordinator]):
    """Entity attached to the per-country service device."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: FirmsCoordinator, unique_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Fire Hotspots",
            model="Data: NASA FIRMS",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=FIRMS_MAP_URL,
        )


class FirmsZoneEntity(FirmsEntity):
    """Entity bound to one watched zone."""

    _zone_translation_key: str

    def __init__(
        self, coordinator: FirmsCoordinator, kind: str, zone: WatchZone
    ) -> None:
        """Initialize the entity for a WatchZone."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, zone_unique_id(entry_id, kind, zone.zone_id))
        self.zone_id = zone.zone_id
        self._attr_translation_key = self._zone_translation_key
        self._attr_translation_placeholders = {"zone": zone.name}


class FirmsConfigEntity(FirmsEntity):
    """Configuration entity that writes one config entry option."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Stay operable even while FIRMS requests fail."""
        return True

    def _set_option(self, key: str, value: Any) -> None:
        """Persist an option and reload the entry to apply it."""
        entry = self.coordinator.config_entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, key: value}
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)


class FirmsRegionEntity(FirmsEntity):
    """Entity bound to one selected region or the whole country."""

    _region_translation_key: str

    def __init__(self, coordinator: FirmsCoordinator, kind: str, region: str) -> None:
        """Initialize the entity."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, region_unique_id(entry_id, kind, region))
        self.region = region
        if region == WHOLE_COUNTRY:
            self._attr_translation_key = f"country_{self._region_translation_key}"
        else:
            self._attr_translation_key = f"region_{self._region_translation_key}"
            self._attr_translation_placeholders = {
                "region": region_name(
                    coordinator.boundaries, region, coordinator.hass.config.language
                )
            }
