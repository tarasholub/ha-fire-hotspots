"""Constants for the Fire Hotspots integration."""

from logging import Logger, getLogger
from typing import Final

LOGGER: Logger = getLogger(__package__)

DOMAIN: Final = "fire_hotspots"
ATTRIBUTION: Final = (
    "Fire data: NASA FIRMS (LANCE). Boundaries: geoBoundaries (gbOpen)."
)
FIRMS_MAP_URL: Final = "https://firms.modaps.eosdis.nasa.gov/map/"
MAP_KEY_URL: Final = "https://firms.modaps.eosdis.nasa.gov/api/map_key/"

# Config entry data
CONF_MAP_KEY: Final = "map_key"
CONF_COUNTRY: Final = "country"  # ISO 3166-1 alpha-2, as HA's country selector

# Config entry options
CONF_REGIONS: Final = "regions"
CONF_HOURS: Final = "hours"
CONF_MIN_CONFIDENCE: Final = "min_confidence"
CONF_SOURCES: Final = "sources"
CONF_SHOW_ON_MAP: Final = "show_on_map"
CONF_UPDATE_INTERVAL: Final = "update_interval"  # minutes

# Pseudo region id for "whole country" (sum of all regions).
WHOLE_COUNTRY: Final = "country"

# Sources: https://firms.modaps.eosdis.nasa.gov/api/area/
SOURCE_VIIRS_SNPP: Final = "VIIRS_SNPP_NRT"
SOURCE_VIIRS_NOAA20: Final = "VIIRS_NOAA20_NRT"
SOURCE_VIIRS_NOAA21: Final = "VIIRS_NOAA21_NRT"
SOURCE_MODIS: Final = "MODIS_NRT"
SOURCES: Final = [
    SOURCE_VIIRS_SNPP,
    SOURCE_VIIRS_NOAA20,
    SOURCE_VIIRS_NOAA21,
    SOURCE_MODIS,
]

CONFIDENCE_LOW: Final = "low"
CONFIDENCE_NOMINAL: Final = "nominal"
CONFIDENCE_HIGH: Final = "high"
CONFIDENCE_LEVELS: Final = [CONFIDENCE_LOW, CONFIDENCE_NOMINAL, CONFIDENCE_HIGH]

# Defaults match SaveEcoBot's counts: all sources, no confidence filter,
# no cross-satellite deduplication, rolling 24 hours.
DEFAULT_HOURS: Final = 24
DEFAULT_MIN_CONFIDENCE: Final = CONFIDENCE_LOW
DEFAULT_SOURCES: Final = SOURCES
DEFAULT_SHOW_ON_MAP: Final = False
DEFAULT_UPDATE_MINUTES: Final = 30

MIN_HOURS: Final = 1
MAX_HOURS: Final = 96  # FIRMS area API allows up to 5 days per request

# FIRMS allows 5000 transactions per 10 minutes; even the 10-minute minimum
# stays far below that with one transaction per source per refresh.
MIN_UPDATE_MINUTES: Final = 10
MAX_UPDATE_MINUTES: Final = 180

# Event entity
EVENT_DETECTED: Final = "detected"
MAX_EVENT_DETECTIONS: Final = 50  # cap attribute size per event

# Attributes
ATTR_ACQUIRED: Final = "acquired"
ATTR_SATELLITE: Final = "satellite"
ATTR_INSTRUMENT: Final = "instrument"
ATTR_CONFIDENCE: Final = "confidence"
ATTR_FRP: Final = "frp"
ATTR_DAYNIGHT: Final = "daynight"
ATTR_REGION: Final = "region"
ATTR_REGION_NAME: Final = "region_name"
ATTR_DISTANCE: Final = "distance"
ATTR_SOURCE: Final = "source"
ATTR_COUNT: Final = "count"
ATTR_DETECTIONS: Final = "detections"
