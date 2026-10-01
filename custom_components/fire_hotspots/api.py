"""Minimal, Home Assistant agnostic client for the NASA FIRMS area API."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from collections.abc import Sequence

API_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
KEY_STATUS_URL = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_DAY_RANGE = 5

# MODIS reports confidence as 0-100, VIIRS as l/n/h. FIRMS documents
# MODIS classes as low (<30), nominal (30-79) and high (>=80).
MODIS_NOMINAL_THRESHOLD = 30
MODIS_HIGH_THRESHOLD = 80
VIIRS_CONFIDENCE = {"l": "low", "n": "nominal", "h": "high"}


class FirmsError(Exception):
    """Base error for the FIRMS client."""


class FirmsConnectionError(FirmsError):
    """FIRMS could not be reached."""


class FirmsAuthError(FirmsError):
    """MAP_KEY is invalid."""


class FirmsRateLimitError(FirmsError):
    """MAP_KEY transaction limit is exceeded."""


@dataclass(frozen=True, slots=True)
class KeyStatus:
    """MAP_KEY usage within the current rate-limit window."""

    used: int
    limit: int
    interval: str


@dataclass(frozen=True, slots=True)
class Hotspot:
    """A single thermal anomaly detection."""

    source: str
    latitude: float
    longitude: float
    acquired: datetime
    satellite: str
    instrument: str
    confidence: str
    frp: float | None
    daynight: str

    @property
    def id(self) -> str:
        """Stable identifier of the detection."""
        return (
            f"{self.source}_{self.acquired:%Y%m%d%H%M}_"
            f"{self.latitude:.4f}_{self.longitude:.4f}"
        )


def normalize_confidence(raw: str) -> str:
    """Map VIIRS (l/n/h) and MODIS (0-100) confidence to low/nominal/high."""
    value = raw.strip().lower()
    if value in VIIRS_CONFIDENCE:
        return VIIRS_CONFIDENCE[value]
    try:
        numeric = int(float(value))
    except ValueError:
        return "nominal"
    if numeric >= MODIS_HIGH_THRESHOLD:
        return "high"
    if numeric >= MODIS_NOMINAL_THRESHOLD:
        return "nominal"
    return "low"


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_csv(source: str, text: str) -> list[Hotspot]:
    """Parse FIRMS CSV output into hotspots, skipping malformed rows."""
    hotspots: list[Hotspot] = []
    for row in csv.DictReader(io.StringIO(text)):
        lat = _float(row.get("latitude"))
        lon = _float(row.get("longitude"))
        date = row.get("acq_date")
        time = (row.get("acq_time") or "").strip().zfill(4)
        if lat is None or lon is None or not date:
            continue
        try:
            acquired = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H%M").replace(
                tzinfo=UTC
            )
        except ValueError:
            continue
        hotspots.append(
            Hotspot(
                source=source,
                latitude=lat,
                longitude=lon,
                acquired=acquired,
                satellite=(row.get("satellite") or "").strip(),
                instrument=(row.get("instrument") or "").strip(),
                confidence=normalize_confidence(row.get("confidence") or ""),
                frp=_float(row.get("frp")),
                daynight=(row.get("daynight") or "").strip(),
            )
        )
    return hotspots


class FirmsClient:
    """Fetch hotspots from the FIRMS area API."""

    def __init__(self, session: aiohttp.ClientSession, map_key: str) -> None:
        """Initialize the client."""
        self._session = session
        self._map_key = map_key

    async def async_get_hotspots(
        self,
        source: str,
        bbox: Sequence[float],
        day_range: int = 1,
    ) -> list[Hotspot]:
        """
        Return hotspots for one source within bbox (west, south, east, north).

        One call costs one MAP_KEY transaction (5000 per 10 minutes).
        """
        day_range = max(1, min(MAX_DAY_RANGE, day_range))
        area = ",".join(f"{v:.4f}" for v in bbox)
        url = f"{API_BASE_URL}/{self._map_key}/{source}/{area}/{day_range}"
        try:
            async with self._session.get(url, timeout=REQUEST_TIMEOUT) as response:
                text = await response.text()
                status = response.status
        except (aiohttp.ClientError, TimeoutError) as err:
            msg = f"Error communicating with FIRMS: {err}"
            raise FirmsConnectionError(msg) from err

        body = text.strip()
        lowered = body[:300].lower()
        if body.startswith("latitude"):
            return parse_csv(source, body)
        if "invalid" in lowered and "key" in lowered:
            msg = "Invalid MAP_KEY"
            raise FirmsAuthError(msg)
        if "transaction" in lowered and ("limit" in lowered or "exceed" in lowered):
            msg = "MAP_KEY transaction limit exceeded"
            raise FirmsRateLimitError(msg)
        if status >= 500:  # noqa: PLR2004
            msg = f"FIRMS server error {status}"
            raise FirmsConnectionError(msg)
        if not body and status < 400:  # noqa: PLR2004
            return []
        msg = f"Unexpected FIRMS response ({status}): {body[:200]}"
        raise FirmsError(msg)

    async def async_get_key_status(self) -> KeyStatus:
        """MAP_KEY usage in the current window; does not cost a transaction."""
        try:
            async with self._session.get(
                KEY_STATUS_URL,
                params={"MAP_KEY": self._map_key},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            msg = f"Error fetching MAP_KEY status: {err}"
            raise FirmsConnectionError(msg) from err
        try:
            return KeyStatus(
                used=int(data["current_transactions"]),
                limit=int(data["transaction_limit"]),
                interval=str(data["transaction_interval"]),
            )
        except (KeyError, TypeError, ValueError) as err:
            msg = "Unexpected MAP_KEY status response"
            raise FirmsError(msg) from err
