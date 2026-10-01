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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FirmsAuthError, FirmsClient, FirmsError, Hotspot
from .boundaries import BBox, CountryBoundaries, distance_km
from .const import (
    CONF_HOURS,
    CONF_MIN_CONFIDENCE,
    CONF_REGIONS,
    CONF_SHOW_ON_MAP,
    CONF_SOURCES,
    CONFIDENCE_LEVELS,
    DEFAULT_HOURS,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_SHOW_ON_MAP,
    DEFAULT_SOURCES,
    DOMAIN,
    LOGGER,
    UPDATE_INTERVAL,
    WHOLE_COUNTRY,
)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY = 10  # seconds


def seen_store_key(entry_id: str) -> str:
    """Storage key for detections already reported for an entry."""
    return f"{DOMAIN}.{entry_id}.seen"


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

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Settings:
        """Build settings from config entry options."""
        return cls(
            regions=list(options.get(CONF_REGIONS, [])),
            hours=int(options.get(CONF_HOURS, DEFAULT_HOURS)),
            min_confidence=options.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
            sources=list(options.get(CONF_SOURCES, DEFAULT_SOURCES)),
            show_on_map=bool(options.get(CONF_SHOW_ON_MAP, DEFAULT_SHOW_ON_MAP)),
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


@dataclass(slots=True)
class FirmsData:
    """Processed coordinator data."""

    detections: list[Detection] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    new: dict[str, list[Detection]] = field(default_factory=dict)


def query_boxes(boundaries: CountryBoundaries, settings: Settings) -> list[BBox]:
    """
    Areas to request from FIRMS: one union box, or two across the antimeridian.

    Union boxes include neighbouring territory; polygons filter it out later.
    """
    ids = None if settings.whole_country else settings.regions
    boxes = boundaries.bboxes(ids)
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


def process(  # noqa: PLR0913
    hotspots: list[Hotspot],
    boundaries: CountryBoundaries,
    settings: Settings,
    *,
    home: tuple[float, float] | None,
    now: datetime,
    seen: set[str] | None,
) -> FirmsData:
    """
    Filter, locate and count hotspots. CPU bound: run in an executor.

    seen holds ids from the previous refresh; None means first refresh, which
    only sets the baseline and reports nothing as new.
    """
    since = now - timedelta(hours=settings.hours)
    min_level = CONFIDENCE_LEVELS.index(settings.min_confidence)
    selected = set(settings.regions) - {WHOLE_COUNTRY}
    data = FirmsData(counts=dict.fromkeys(settings.regions, 0))
    unique: dict[str, Detection] = {}

    for hotspot in hotspots:
        if hotspot.id in unique or hotspot.acquired <= since:
            continue
        if CONFIDENCE_LEVELS.index(hotspot.confidence) < min_level:
            continue
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
    if seen is not None:
        for detection in data.detections:
            if detection.id not in seen:
                data.new.setdefault(detection.region, []).append(detection)
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
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.boundaries = boundaries
        self.settings = Settings.from_options(dict(config_entry.options))
        self._seen: set[str] | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, seen_store_key(config_entry.entry_id)
        )

    @property
    def _fingerprint(self) -> str:
        """Settings that define which detections are in scope."""
        s = self.settings
        return "|".join(
            [",".join(sorted(s.regions)), ",".join(sorted(s.sources)), s.min_confidence]
        )

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
        requests = [
            self.client.async_get_hotspots(source, box, s.day_range)
            for source in s.sources
            for box in query_boxes(self.boundaries, s)
        ]
        try:
            results = await asyncio.gather(*requests)
        except FirmsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FirmsError as err:
            raise UpdateFailed(str(err)) from err

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
            )
        )
        self._seen = {d.id for d in data.detections}
        self._store.async_delay_save(self._stored_data, STORAGE_SAVE_DELAY)
        return data

    @callback
    def _stored_data(self) -> dict[str, Any]:
        return {"fingerprint": self._fingerprint, "seen": sorted(self._seen or [])}
