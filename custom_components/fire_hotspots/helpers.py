"""Home Assistant glue for boundaries: cache location, download, parsing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .boundaries import (
    CountryBoundaries,
    ProgressCallback,
    async_download,
    load_cached,
    parse_geojson,
    store_cache,
)
from .const import DOMAIN
from .countries import COUNTRIES

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def cache_dir(hass: HomeAssistant) -> Path:
    """Directory with cached boundaries (included in HA backups)."""
    return Path(hass.config.path(".storage", DOMAIN))


async def async_get_boundaries(
    hass: HomeAssistant,
    country: str,
    progress: ProgressCallback | None = None,
) -> CountryBoundaries:
    """
    Return boundaries for an ISO 3166-1 alpha-2 country, downloading if needed.

    Raises BoundariesError (or BoundariesNotFoundError) on failure.
    """
    iso3 = COUNTRIES[country].iso3
    directory = cache_dir(hass)
    cached = await hass.async_add_executor_job(load_cached, directory, iso3)
    if cached is not None:
        return cached
    level, geojson = await async_download(async_get_clientsession(hass), iso3, progress)
    data = await hass.async_add_executor_job(parse_geojson, iso3, level, geojson)
    return await hass.async_add_executor_job(store_cache, directory, data)
