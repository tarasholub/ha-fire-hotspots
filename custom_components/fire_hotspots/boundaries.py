"""
Country and region boundaries from geoBoundaries (gbOpen).

Home Assistant agnostic: the caller provides an aiohttp session and a cache
directory. Files are downloaded once per country, simplified, and cached as
compact JSON. Point lookup is pure Python and CPU bound, so run it in an
executor for large batches.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from pathlib import Path

# Pinned geoBoundaries commit: every install gets the same boundaries.
# Bump deliberately (and CACHE_VERSION) to pick up upstream changes.
SOURCE_COMMIT = "5c25134028196d43ce97b5071934fd0cfc92f09f"
SOURCE_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/"
    + SOURCE_COMMIT
    + "/releaseData/gbOpen/{iso3}/{level}/"
    "geoBoundaries-{iso3}-{level}_simplified.geojson"
)
ATTRIBUTION = "geoBoundaries (gbOpen), www.geoboundaries.org"
LEVEL_REGIONS = "ADM1"
LEVEL_COUNTRY = "ADM0"
CACHE_VERSION = 1

TIMEOUT = aiohttp.ClientTimeout(total=300, sock_read=60)
CHUNK_SIZE = 64 * 1024
SIMPLIFY_TOLERANCE = 0.005  # degrees, ~0.5 km
PRECISION = 4  # ~10 m
EARTH_RADIUS_KM = 6371.0088

type Point = list[float]  # [lon, lat]
type Ring = list[Point]
type Polygon = list[Ring]  # exterior ring followed by holes
type BBox = tuple[float, float, float, float]  # west, south, east, north
type ProgressCallback = Callable[[float], None]

# geoBoundaries names are English; Ukrainian names for Ukraine.
NAMES_UK: dict[str, str] = {
    "UA-05": "Вінницька область",
    "UA-07": "Волинська область",
    "UA-09": "Луганська область",
    "UA-12": "Дніпропетровська область",
    "UA-14": "Донецька область",
    "UA-18": "Житомирська область",
    "UA-21": "Закарпатська область",
    "UA-23": "Запорізька область",
    "UA-26": "Івано-Франківська область",
    "UA-30": "Київ",
    "UA-32": "Київська область",
    "UA-35": "Кіровоградська область",
    "UA-40": "Севастополь",
    "UA-43": "Автономна Республіка Крим",
    "UA-46": "Львівська область",
    "UA-48": "Миколаївська область",
    "UA-51": "Одеська область",
    "UA-53": "Полтавська область",
    "UA-56": "Рівненська область",
    "UA-59": "Сумська область",
    "UA-61": "Тернопільська область",
    "UA-63": "Харківська область",
    "UA-65": "Херсонська область",
    "UA-68": "Хмельницька область",
    "UA-71": "Черкаська область",
    "UA-74": "Чернігівська область",
    "UA-77": "Чернівецька область",
}


UK_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
_UK_ORDER = {c: i for i, c in enumerate(UK_ALPHABET)}


def name_sort_key(name: str) -> tuple[int, ...]:
    """
    Sort key that orders Ukrainian names by the Ukrainian alphabet.

    Plain string sorting puts "І" (U+0406) before "А" (U+0410). Other
    characters keep their code point order, after the Ukrainian letters.
    """
    return tuple(_UK_ORDER.get(c, len(UK_ALPHABET) + ord(c)) for c in name.casefold())


class BoundariesError(Exception):
    """Boundaries could not be downloaded or parsed."""


class BoundariesNotFoundError(BoundariesError):
    """geoBoundaries has no data for this country."""


# --- geometry -------------------------------------------------------------


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _point_segment_dist2(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return (p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2
    t = max(
        0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy))
    )
    return (p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2


def simplify_ring(ring: Ring, tolerance: float = SIMPLIFY_TOLERANCE) -> Ring:
    """Simplify a closed ring with Douglas-Peucker, keeping at least 4 points."""
    if len(ring) <= 4:  # noqa: PLR2004
        return ring
    tol2 = tolerance * tolerance
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        start, end = stack.pop()
        best, index = 0.0, -1
        for i in range(start + 1, end):
            d = _point_segment_dist2(ring[i], ring[start], ring[end])
            if d > best:
                best, index = d, i
        if index != -1 and best > tol2:
            keep[index] = True
            stack.extend(((start, index), (index, end)))
    result = [p for p, k in zip(ring, keep, strict=True) if k]
    return result if len(result) >= 4 else ring  # noqa: PLR2004


def _in_ring(lon: float, lat: float, ring: Ring) -> bool:
    """Ray casting point-in-ring test."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def polygon_bboxes(polygons: list[Polygon]) -> list[BBox]:
    """
    Bounding boxes for a region, split at the antimeridian.

    A region spanning more than 180 degrees of longitude (e.g. Chukotka,
    Alaska, Fiji) gets one box for eastern and one for western longitudes.
    """
    points = [p for poly in polygons for p in poly[0]]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    if max(lons) - min(lons) <= 180:  # noqa: PLR2004
        return [(min(lons), min(lats), max(lons), max(lats))]
    east = [p for p in points if p[0] >= 0]
    west = [p for p in points if p[0] < 0]
    return [
        (
            min(p[0] for p in part),
            min(p[1] for p in part),
            max(p[0] for p in part),
            max(p[1] for p in part),
        )
        for part in (east, west)
        if part
    ]


# --- model ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Region:
    """A region (or the whole country) with simplified polygons."""

    id: str
    names: dict[str, str]
    bboxes: list[BBox]
    polygons: list[Polygon] = field(repr=False)

    def name(self, language: str | None) -> str:
        """Localized name with English fallback."""
        lang = (language or "en").split("-")[0]
        return self.names.get(lang) or self.names["en"]

    def contains(self, lat: float, lon: float) -> bool:
        """Return True if the point is inside the region."""
        if not any(w <= lon <= e and s <= lat <= n for w, s, e, n in self.bboxes):
            return False
        return any(
            _in_ring(lon, lat, exterior)
            and not any(_in_ring(lon, lat, hole) for hole in holes)
            for exterior, *holes in self.polygons
        )


@dataclass(frozen=True, slots=True)
class CountryBoundaries:
    """Regions of one country. level is ADM1, or ADM0 when no regions exist."""

    country: str
    level: str
    regions: dict[str, Region]

    def find(self, lat: float, lon: float) -> Region | None:
        """Region containing the point, or None if outside the country."""
        for region in self.regions.values():
            if region.contains(lat, lon):
                return region
        return None

    def bboxes(self, region_ids: Iterable[str] | None = None) -> list[BBox]:
        """Query boxes for the given regions (all regions when None)."""
        ids = self.regions.keys() if region_ids is None else region_ids
        return [
            b for rid in ids if rid in self.regions for b in self.regions[rid].bboxes
        ]


# --- parsing and cache ----------------------------------------------------


def _round_ring(ring: Iterable[Iterable[float]]) -> Ring:
    out: Ring = []
    for lon, lat, *_ in ring:
        point = [round(lon, PRECISION), round(lat, PRECISION)]
        if not out or out[-1] != point:
            out.append(point)
    return out


def _geometry_polygons(geometry: dict[str, Any]) -> list[Polygon]:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    raw = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    polygons: list[Polygon] = []
    for poly in raw:
        rings = [simplify_ring(_round_ring(r)) for r in poly]
        rings = [r for r in rings if len(r) >= 4]  # noqa: PLR2004
        if rings:
            polygons.append(rings)
    return polygons


def parse_geojson(country: str, level: str, data: dict[str, Any]) -> dict[str, Any]:
    """Convert geoBoundaries GeoJSON to the compact cache format. CPU bound."""
    regions: dict[str, dict[str, Any]] = {}
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        polygons = _geometry_polygons(feature.get("geometry") or {})
        if not polygons:
            continue
        if level == LEVEL_COUNTRY:
            region_id = country
        else:
            iso = (props.get("shapeISO") or "").strip()
            region_id = iso if iso and iso not in regions else props.get("shapeID")
        names = {"en": props.get("shapeName") or region_id}
        if region_id in NAMES_UK:
            names["uk"] = NAMES_UK[region_id]
        if region_id in regions:  # ADM0 split into several features
            regions[region_id]["polygons"].extend(polygons)
        else:
            regions[region_id] = {"names": names, "polygons": polygons}
    if not regions:
        msg = f"No usable geometry for {country} {level}"
        raise BoundariesError(msg)
    return {
        "version": CACHE_VERSION,
        "country": country,
        "level": level,
        "regions": regions,
    }


def from_cache(data: dict[str, Any]) -> CountryBoundaries:
    """Build boundaries from the compact cache format."""
    regions = {
        rid: Region(
            id=rid,
            names=item["names"],
            bboxes=polygon_bboxes(item["polygons"]),
            polygons=item["polygons"],
        )
        for rid, item in data["regions"].items()
    }
    regions = dict(sorted(regions.items(), key=lambda kv: kv[1].names["en"]))
    return CountryBoundaries(
        country=data["country"], level=data["level"], regions=regions
    )


def cache_path(cache_dir: Path, country: str) -> Path:
    """Cache file for a country (ISO 3166-1 alpha-3)."""
    return cache_dir / f"{country}.json"


def load_cached(cache_dir: Path, country: str) -> CountryBoundaries | None:
    """Load cached boundaries, or None if missing/outdated. Blocking I/O."""
    path = cache_path(cache_dir, country)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    return from_cache(data)


def store_cache(cache_dir: Path, data: dict[str, Any]) -> CountryBoundaries:
    """Write the compact format atomically and return parsed boundaries."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, data["country"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), "utf-8")
    tmp.replace(path)
    return from_cache(data)


# --- download -------------------------------------------------------------


async def _fetch(
    session: aiohttp.ClientSession,
    url: str,
    progress: ProgressCallback | None,
) -> bytes | None:
    """Download a file, reporting progress 0..1. None on 404."""
    try:
        async with session.get(url, timeout=TIMEOUT) as response:
            if response.status == 404:  # noqa: PLR2004
                return None
            if response.status != 200:  # noqa: PLR2004
                msg = f"geoBoundaries returned HTTP {response.status}"
                raise BoundariesError(msg)
            total = int(response.headers.get("Content-Length") or 0)
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                chunks.append(chunk)
                received += len(chunk)
                if progress and total:
                    progress(min(received / total, 1.0))
            return b"".join(chunks)
    except (aiohttp.ClientError, TimeoutError) as err:
        msg = f"Error downloading boundaries: {err}"
        raise BoundariesError(msg) from err


async def async_download(
    session: aiohttp.ClientSession,
    country: str,
    progress: ProgressCallback | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Download regions (ADM1), falling back to the country outline (ADM0).

    Returns (level, geojson). Parsing into the cache format is CPU bound and
    left to the caller (run parse_geojson in an executor).
    """
    for level in (LEVEL_REGIONS, LEVEL_COUNTRY):
        raw = await _fetch(
            session, SOURCE_URL.format(iso3=country, level=level), progress
        )
        if raw is None:
            continue
        try:
            return level, json.loads(raw)
        except ValueError as err:
            msg = f"Invalid GeoJSON for {country} {level}"
            raise BoundariesError(msg) from err
    msg = f"geoBoundaries has no data for {country}"
    raise BoundariesNotFoundError(msg)
