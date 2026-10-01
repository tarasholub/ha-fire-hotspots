"""Data update coordinator for Fire Hotspots."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .api import (
    FirmsAuthError,
    FirmsClient,
    FirmsError,
    FirmsRateLimitError,
    Hotspot,
    KeyStatus,
)
from .boundaries import BBox, CountryBoundaries, distance_km
from .const import (
    CONF_HOURS,
    CONF_MIN_CONFIDENCE,
    CONF_POINTS,
    CONF_REGIONS,
    CONF_SHOW_ON_MAP,
    CONF_SOURCES,
    CONF_UPDATE_INTERVAL,
    CONF_ZONE_RADIUS,
    CONF_ZONES,
    CONFIDENCE_LEVELS,
    DEFAULT_HOURS,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_SHOW_ON_MAP,
    DEFAULT_SOURCES,
    DEFAULT_UPDATE_MINUTES,
    DEFAULT_ZONE_RADIUS_KM,
    DOMAIN,
    LOGGER,
    WHOLE_COUNTRY,
)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY = 10  # seconds


def seen_store_key(entry_id: str) -> str:
    """Storage key for detections already reported for an entry."""
    return f"{DOMAIN}.{entry_id}.seen"


def rate_limit_issue_id(entry_id: str) -> str:
    """Repairs issue id for a rate-limited entry."""
    return f"rate_limit_{entry_id}"


def point_zone_id(name: str) -> str:
    """Pseudo zone id of a watch point; pt- prefix avoids zone collisions."""
    return f"point.pt-{slugify(name)}"


if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import FirmsConfigEntry


@dataclass(frozen=True, slots=True)
class Settings:
    """Effective options of a config entry."""

    regions: list[str]
    hours: int
    min_confidence: str
    sources: list[str]
    show_on_map: bool
    update_minutes: int
    zones: list[str]
    zone_radius: float  # km
    points: list[dict[str, Any]]  # name, latitude, longitude, radius (km)

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Settings:
        """Build settings from config entry options."""
        return cls(
            regions=list(options.get(CONF_REGIONS, [])),
            hours=int(options.get(CONF_HOURS, DEFAULT_HOURS)),
            min_confidence=options.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
            sources=list(options.get(CONF_SOURCES, DEFAULT_SOURCES)),
            show_on_map=bool(options.get(CONF_SHOW_ON_MAP, DEFAULT_SHOW_ON_MAP)),
            update_minutes=int(
                options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_MINUTES)
            ),
            zones=list(options.get(CONF_ZONES, [])),
            zone_radius=float(options.get(CONF_ZONE_RADIUS, DEFAULT_ZONE_RADIUS_KM)),
            points=list(options.get(CONF_POINTS, [])),
        )

    @property
    def whole_country(self) -> bool:
        """Whether the whole country is monitored."""
        return WHOLE_COUNTRY in self.regions

    @property
    def day_range(self) -> int:
        """FIRMS days to request: UTC days covering the window, plus one."""
        return min(5, math.ceil(self.hours / 24) + 1)


@dataclass(frozen=True, slots=True)
class Detection:
    """Hotspot inside a monitored region."""

    hotspot: Hotspot
    region: str
    distance: float | None  # km from Home Assistant home, if configured

    @property
    def id(self) -> str:
        """Stable identifier."""
        return self.hotspot.id


@dataclass(frozen=True, slots=True)
class WatchZone:
    """A Home Assistant zone watched within a radius."""

    zone_id: str  # zone entity id
    name: str
    latitude: float
    longitude: float
    radius: float  # km


@dataclass(frozen=True, slots=True)
class ZoneDetection:
    """Hotspot within a watched zone's radius."""

    hotspot: Hotspot
    distance: float  # km from the zone centre

    @property
    def id(self) -> str:
        """Stable identifier."""
        return self.hotspot.id


@dataclass(slots=True)
class ZoneData:
    """Hotspots around one watched zone."""

    detections: list[ZoneDetection] = field(default_factory=list)
    new: list[ZoneDetection] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of hotspots within the radius."""
        return len(self.detections)

    @property
    def nearest(self) -> float | None:
        """Distance to the nearest hotspot, km."""
        if not self.detections:
            return None
        return min(d.distance for d in self.detections)


@dataclass(slots=True)
class FirmsData:
    """Processed coordinator data."""

    detections: list[Detection] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    new: dict[str, list[Detection]] = field(default_factory=dict)
    zones: dict[str, ZoneData] = field(default_factory=dict)
    latest_by_source: dict[str, datetime | None] = field(default_factory=dict)
    key_status: KeyStatus | None = None


def zone_bbox(zone: WatchZone) -> BBox:
    """Bounding box of a zone's radius circle."""
    dlat = zone.radius / 111.2
    dlon = zone.radius / (111.2 * max(math.cos(math.radians(zone.latitude)), 0.01))
    return (
        zone.longitude - dlon,
        zone.latitude - dlat,
        zone.longitude + dlon,
        zone.latitude + dlat,
    )


def query_boxes(
    boundaries: CountryBoundaries,
    settings: Settings,
    zones: list[WatchZone] | None = None,
) -> list[BBox]:
    """
    Areas to request from FIRMS: one union box, or two across the antimeridian.

    Union boxes include neighbouring territory; polygons filter it out later.
    Watched zones extend the area so nearby fires count regardless of region.
    """
    ids = None if settings.whole_country else settings.regions
    boxes = boundaries.bboxes(ids) + [zone_bbox(zone) for zone in zones or []]
    if not boxes:
        return []
    groups = [boxes]
    if max(b[2] for b in boxes) - min(b[0] for b in boxes) > 180:  # noqa: PLR2004
        groups = [[b for b in boxes if b[0] >= 0], [b for b in boxes if b[0] < 0]]
    return [
        (
            min(b[0] for b in group),
            min(b[1] for b in group),
            max(b[2] for b in group),
            max(b[3] for b in group),
        )
        for group in groups
        if group
    ]


def _assign_to_zones(hotspot: Hotspot, zones: list[WatchZone], data: FirmsData) -> None:
    """Attach the hotspot to every watched zone whose radius covers it."""
    for zone in zones:
        zone_distance = distance_km(
            zone.latitude, zone.longitude, hotspot.latitude, hotspot.longitude
        )
        if zone_distance <= zone.radius:
            data.zones[zone.zone_id].detections.append(
                ZoneDetection(hotspot, round(zone_distance, 2))
            )


def _collect_new(data: FirmsData, seen: set[str] | None) -> None:
    """Fill per-region and per-zone lists of detections not seen before."""
    if seen is None:
        return
    for detection in data.detections:
        if detection.id not in seen:
            data.new.setdefault(detection.region, []).append(detection)
    for zone_data in data.zones.values():
        zone_data.new = [d for d in zone_data.detections if d.id not in seen]


def process(  # noqa: PLR0913
    hotspots: list[Hotspot],
    boundaries: CountryBoundaries,
    settings: Settings,
    *,
    home: tuple[float, float] | None,
    now: datetime,
    seen: set[str] | None,
    zones: list[WatchZone] | None = None,
) -> FirmsData:
    """
    Filter, locate and count hotspots. CPU bound: run in an executor.

    seen holds ids from the previous refresh; None means first refresh, which
    only sets the baseline and reports nothing as new. Zones count hotspots by
    distance alone, regardless of regions and country borders.
    """
    zones = zones or []
    since = now - timedelta(hours=settings.hours)
    min_level = CONFIDENCE_LEVELS.index(settings.min_confidence)
    selected = set(settings.regions) - {WHOLE_COUNTRY}
    data = FirmsData(
        counts=dict.fromkeys(settings.regions, 0),
        zones={zone.zone_id: ZoneData() for zone in zones},
    )
    unique: dict[str, Detection] = {}
    done: set[str] = set()

    for hotspot in hotspots:
        if hotspot.id in done or hotspot.acquired <= since:
            continue
        if CONFIDENCE_LEVELS.index(hotspot.confidence) < min_level:
            continue
        done.add(hotspot.id)
        _assign_to_zones(hotspot, zones, data)
        region = boundaries.find(hotspot.latitude, hotspot.longitude)
        if region is None:
            continue
        in_selected = region.id in selected
        if not (in_selected or settings.whole_country):
            continue
        distance = (
            distance_km(home[0], home[1], hotspot.latitude, hotspot.longitude)
            if home
            else None
        )
        unique[hotspot.id] = Detection(hotspot, region.id, distance)
        if in_selected:
            data.counts[region.id] += 1
        if settings.whole_country:
            data.counts[WHOLE_COUNTRY] += 1

    data.detections = sorted(unique.values(), key=lambda d: d.hotspot.acquired)
    for zone_data in data.zones.values():
        zone_data.detections.sort(key=lambda d: d.distance)
    _collect_new(data, seen)
    return data


class FirmsCoordinator(DataUpdateCoordinator[FirmsData]):
    """Poll FIRMS and derive per-region data."""

    config_entry: FirmsConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: FirmsConfigEntry,
        client: FirmsClient,
        boundaries: CountryBoundaries,
    ) -> None:
        """Initialize the coordinator."""
        self.settings = Settings.from_options(dict(config_entry.options))
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=self.settings.update_minutes),
        )
        self.client = client
        self.boundaries = boundaries
        self._seen: set[str] | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, seen_store_key(config_entry.entry_id)
        )

    @property
    def _fingerprint(self) -> str:
        """Settings that define which detections are in scope."""
        s = self.settings
        points = sorted(
            ":".join(
                str(p.get(key)) for key in ("name", "latitude", "longitude", "radius")
            )
            for p in s.points
        )
        return "|".join(
            [
                ",".join(sorted(s.regions)),
                ",".join(sorted(s.sources)),
                s.min_confidence,
                ",".join(sorted(s.zones)),
                str(s.zone_radius),
                ",".join(points),
            ]
        )

    @callback
    def watch_zones(self) -> list[WatchZone]:
        """Watched zones and points; broken points and missing zones skipped."""
        zones = []
        for point in self.settings.points:
            try:
                zones.append(
                    WatchZone(
                        zone_id=point_zone_id(point["name"]),
                        name=point["name"],
                        latitude=float(point["latitude"]),
                        longitude=float(point["longitude"]),
                        radius=float(point["radius"]),
                    )
                )
            except KeyError, TypeError, ValueError:
                LOGGER.warning("Invalid watch point %s, skipping", point)
        for zone_id in self.settings.zones:
            state = self.hass.states.get(zone_id)
            if state is None or "latitude" not in state.attributes:
                LOGGER.warning("Watched zone %s not found, skipping", zone_id)
                continue
            zones.append(
                WatchZone(
                    zone_id=zone_id,
                    name=state.attributes.get("friendly_name")
                    or zone_id.partition(".")[2],
                    latitude=state.attributes["latitude"],
                    longitude=state.attributes["longitude"],
                    radius=self.settings.zone_radius,
                )
            )
        return zones

    async def _async_setup(self) -> None:
        """
        Restore detections seen before a restart.

        Detections that appeared while Home Assistant was down are then still
        reported as new. A changed scope (regions, sources, confidence) starts a
        fresh baseline instead of reporting everything in it as new.
        """
        stored = await self._store.async_load()
        if stored and stored.get("fingerprint") == self._fingerprint:
            self._seen = set(stored.get("seen", []))

    async def _async_update_data(self) -> FirmsData:
        s = self.settings
        zones = self.watch_zones()
        boxes = query_boxes(self.boundaries, s, zones)
        request_sources = [source for source in s.sources for _ in boxes]
        requests = [
            self.client.async_get_hotspots(source, box, s.day_range)
            for source in s.sources
            for box in boxes
        ]
        entry_id = self.config_entry.entry_id
        try:
            results = await asyncio.gather(*requests)
        except FirmsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FirmsRateLimitError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                rate_limit_issue_id(entry_id),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="rate_limit",
                translation_placeholders={"country": self.config_entry.title},
            )
            raise UpdateFailed(str(err)) from err
        except FirmsError as err:
            raise UpdateFailed(str(err)) from err
        ir.async_delete_issue(self.hass, DOMAIN, rate_limit_issue_id(entry_id))

        latest_by_source: dict[str, datetime | None] = dict.fromkeys(s.sources)
        for source, result in zip(request_sources, results, strict=True):
            newest = max((h.acquired for h in result), default=None)
            current = latest_by_source[source]
            if newest and (current is None or newest > current):
                latest_by_source[source] = newest

        config = self.hass.config
        home = (config.latitude, config.longitude) if config.latitude else None
        data = await self.hass.async_add_executor_job(
            partial(
                process,
                [h for result in results for h in result],
                self.boundaries,
                s,
                home=home,
                now=dt_util.utcnow(),
                seen=self._seen,
                zones=zones,
            )
        )
        data.latest_by_source = latest_by_source
        try:
            data.key_status = await self.client.async_get_key_status()
        except FirmsError as err:
            LOGGER.debug("MAP_KEY status unavailable: %s", err)
        self._seen = {d.id for d in data.detections} | {
            d.id for zone_data in data.zones.values() for d in zone_data.detections
        }
        self._store.async_delay_save(self._stored_data, STORAGE_SAVE_DELAY)
        return data

    @callback
    def _stored_data(self) -> dict[str, Any]:
        return {"fingerprint": self._fingerprint, "seen": sorted(self._seen or [])}
