"""Sensors: hotspot counts per region and distance to the nearest hotspot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfPower

from .const import (
    ATTR_ACQUIRED,
    ATTR_FRP_SUM,
    ATTR_LATEST_ACQUIRED,
    ATTR_NEAREST,
    ATTR_REGION,
    WHOLE_COUNTRY,
)
from .entity import (
    KEY_FRP,
    KEY_LAST,
    KEY_NEAREST,
    KEY_USAGE,
    KIND_COUNT,
    KIND_ZONE_COUNT,
    KIND_ZONE_NEAREST,
    FirmsEntity,
    FirmsRegionEntity,
    FirmsZoneEntity,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import Detection, FirmsCoordinator, WatchZone, ZoneData
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
    entities: list[SensorEntity] = [
        NearestHotspotSensor(coordinator),
        LastDetectionSensor(coordinator),
        TotalFrpSensor(coordinator),
        KeyUsageSensor(coordinator),
    ]
    entities.extend(
        HotspotCountSensor(coordinator, region)
        for region in coordinator.settings.regions
    )
    for zone in coordinator.watch_zones():
        entities.append(ZoneCountSensor(coordinator, zone))
        entities.append(ZoneNearestSensor(coordinator, zone))
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

    @property
    def extra_state_attributes(self) -> dict[str, float | str | None]:
        """Total FRP and newest acquisition time of the region's hotspots."""
        detections = self.coordinator.data.detections
        if self.region != WHOLE_COUNTRY:
            detections = [d for d in detections if d.region == self.region]
        latest = detections[-1].hotspot.acquired if detections else None
        return {
            ATTR_FRP_SUM: round(sum(d.hotspot.frp or 0.0 for d in detections), 1),
            ATTR_LATEST_ACQUIRED: latest.isoformat() if latest else None,
        }


class ZoneCountSensor(FirmsZoneEntity, SensorEntity):
    """Hotspots within the radius of a watched zone."""

    _zone_translation_key = "zone_hotspots"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FirmsCoordinator, zone: WatchZone) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, KIND_ZONE_COUNT, zone)

    def _zone_data(self) -> ZoneData | None:
        return self.coordinator.data.zones.get(self.zone_id)

    @property
    def native_value(self) -> int | None:
        """Number of hotspots within the radius."""
        data = self._zone_data()
        return data.count if data else None

    @property
    def extra_state_attributes(self) -> dict[str, float | str | None]:
        """Distance to the nearest hotspot and total FRP in the radius."""
        data = self._zone_data()
        if data is None:
            return {}
        return {
            ATTR_NEAREST: data.nearest,
            ATTR_FRP_SUM: round(sum(d.hotspot.frp or 0.0 for d in data.detections), 1),
        }


class ZoneNearestSensor(FirmsZoneEntity, SensorEntity):
    """Distance from a watched zone to its nearest hotspot."""

    _zone_translation_key = "zone_nearest"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: FirmsCoordinator, zone: WatchZone) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, KIND_ZONE_NEAREST, zone)

    @property
    def native_value(self) -> float | None:
        """Distance in km; unknown while the radius is clear."""
        data = self.coordinator.data.zones.get(self.zone_id)
        return data.nearest if data else None


class LastDetectionSensor(FirmsEntity, SensorEntity):
    """Acquisition time of the newest detection in the monitored regions."""

    _attr_translation_key = "last_detection"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the sensor."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_LAST}")

    @property
    def native_value(self) -> datetime | None:
        """Acquisition time of the newest detection; detections are sorted."""
        detections = self.coordinator.data.detections
        return detections[-1].hotspot.acquired if detections else None


class TotalFrpSensor(FirmsEntity, SensorEntity):
    """Total fire radiative power over the monitored regions."""

    _attr_translation_key = "total_frp"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.MEGA_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the sensor."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_FRP}")

    @property
    def native_value(self) -> float:
        """Sum of FRP (MW) over all detections; 0 without hotspots."""
        detections = self.coordinator.data.detections
        return round(sum(d.hotspot.frp or 0.0 for d in detections), 1)


class KeyUsageSensor(FirmsEntity, SensorEntity):
    """MAP_KEY transactions used in the current rate-limit window."""

    _attr_translation_key = "key_usage"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FirmsCoordinator) -> None:
        """Initialize the sensor."""
        entry_id = coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{entry_id}_{KEY_USAGE}")

    @property
    def native_value(self) -> int | None:
        """Transactions used; unknown while the status endpoint is down."""
        status = self.coordinator.data.key_status
        return status.used if status else None

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Limit and window of the rate limit."""
        status = self.coordinator.data.key_status
        if status is None:
            return {}
        return {"limit": status.limit, "interval": status.interval}


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
