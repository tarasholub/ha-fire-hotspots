"""Tests for boundaries download, cache and lookup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.fire_hotspots.boundaries import (
    SOURCE_URL,
    BoundariesError,
    BoundariesNotFoundError,
    async_download,
    distance_km,
    load_cached,
    name_sort_key,
    parse_geojson,
    polygon_bboxes,
    simplify_ring,
    store_cache,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

FIXTURES = Path(__file__).parent / "fixtures"

SQUARE = [[0, 0], [0, 1], [0.5, 1.0001], [1, 1], [1, 0], [0, 0]]
GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "properties": {"shapeISO": "XX-01", "shapeID": "A", "shapeName": "One"},
            "geometry": {"type": "Polygon", "coordinates": [SQUARE]},
        },
        {
            "properties": {"shapeISO": "", "shapeID": "B", "shapeName": "Two"},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[2, 0], [2, 1], [3, 1], [3, 0], [2, 0]]]],
            },
        },
        {"properties": {"shapeID": "C"}, "geometry": None},
    ],
}


@pytest.fixture
def ukraine(tmp_path: Path) -> Path:
    """Cache dir with real (simplified) Ukrainian boundaries."""
    shutil.copy(FIXTURES / "UKR.json", tmp_path / "UKR.json")
    return tmp_path


@pytest.mark.parametrize(
    ("lat", "lon", "region", "name_uk"),
    [
        (50.4501, 30.5234, "UA-30", "Київ"),  # Kyiv city: a hole in Kyiv Oblast
        (50.5433, 30.2128, "UA-32", "Київська область"),  # Bucha
        (44.9521, 34.1024, "UA-43", "Автономна Республіка Крим"),  # Simferopol
        (44.6166, 33.5254, "UA-40", "Севастополь"),
        (48.0159, 37.8028, "UA-14", "Донецька область"),  # Donetsk
        (47.0971, 37.5434, "UA-14", "Донецька область"),  # Mariupol
        (52.2297, 21.0122, None, None),  # Warsaw
    ],
)
def test_ukraine_lookup(
    ukraine: Path, lat: float, lon: float, region: str | None, name_uk: str | None
) -> None:
    """Occupied territories resolve to Ukrainian regions."""
    boundaries = load_cached(ukraine, "UKR")
    assert boundaries is not None
    assert len(boundaries.regions) == 27
    found = boundaries.find(lat, lon)
    assert (found.id if found else None) == region
    if found:
        assert found.name("uk") == name_uk


def test_parse_and_cache_roundtrip(tmp_path: Path) -> None:
    """Features become regions; missing ISO falls back to shapeID."""
    data = parse_geojson("XXX", "ADM1", GEOJSON)
    boundaries = store_cache(tmp_path, data)
    assert list(boundaries.regions) == ["XX-01", "B"]
    assert boundaries.regions["B"].name("uk") == "Two"
    assert boundaries.find(0.5, 0.5).id == "XX-01"
    assert boundaries.find(0.5, 2.5).id == "B"
    assert boundaries.find(0.5, 1.5) is None
    assert boundaries.bboxes(["B", "missing"]) == [(2, 0, 3, 1)]
    assert load_cached(tmp_path, "XXX") == boundaries


def test_parse_country_level() -> None:
    """ADM0 features merge into one region keyed by country."""
    data = parse_geojson("XXX", "ADM0", GEOJSON)
    assert list(data["regions"]) == ["XXX"]
    assert len(data["regions"]["XXX"]["polygons"]) == 2


def test_parse_empty() -> None:
    """No geometry is an error."""
    with pytest.raises(BoundariesError):
        parse_geojson("XXX", "ADM1", {"features": []})


def test_load_cached_missing_or_outdated(tmp_path: Path) -> None:
    """Missing, corrupt or old cache files are ignored."""
    assert load_cached(tmp_path, "XXX") is None
    (tmp_path / "XXX.json").write_text("{bad", encoding="utf-8")
    assert load_cached(tmp_path, "XXX") is None
    (tmp_path / "XXX.json").write_text(json.dumps({"version": 0}), encoding="utf-8")
    assert load_cached(tmp_path, "XXX") is None


def test_simplify_and_antimeridian() -> None:
    """Douglas-Peucker drops near-collinear points; boxes split at 180°."""
    assert simplify_ring(SQUARE, 0.01) == [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]
    assert simplify_ring(SQUARE[:4]) == SQUARE[:4]
    fiji = [
        [[[177, -18], [180, -18], [180, -16], [177, -16], [177, -18]]],
        [[[-180, -17], [-179, -17], [-179, -16], [-180, -16], [-180, -17]]],
    ]
    assert polygon_bboxes(fiji) == [(177, -18, 180, -16), (-180, -17, -179, -16)]


def test_distance() -> None:
    """Kyiv-Lviv is ~468 km."""
    assert distance_km(50.4501, 30.5234, 49.8397, 24.0297) == pytest.approx(468, abs=5)


def _url(level: str) -> str:
    return SOURCE_URL.format(iso3="XXX", level=level)


async def test_download_regions_with_progress(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """ADM1 is preferred and progress is reported."""
    body = json.dumps(GEOJSON)
    aioclient_mock.get(
        _url("ADM1"), text=body, headers={"Content-Length": str(len(body))}
    )
    progress: list[float] = []
    level, data = await async_download(
        async_get_clientsession(hass), "XXX", progress.append
    )
    assert level == "ADM1"
    assert len(data["features"]) == 3
    assert progress
    assert progress[-1] == 1.0


async def test_download_falls_back_to_country(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without ADM1 the country outline is used."""
    aioclient_mock.get(_url("ADM1"), status=404)
    aioclient_mock.get(_url("ADM0"), text=json.dumps(GEOJSON))
    level, _ = await async_download(async_get_clientsession(hass), "XXX")
    assert level == "ADM0"


async def test_download_not_found(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No data at all raises BoundariesNotFoundError."""
    aioclient_mock.get(_url("ADM1"), status=404)
    aioclient_mock.get(_url("ADM0"), status=404)
    with pytest.raises(BoundariesNotFoundError):
        await async_download(async_get_clientsession(hass), "XXX")


@pytest.mark.parametrize(
    "kwargs", [{"status": 500}, {"text": "not json"}, {"exc": TimeoutError()}]
)
async def test_download_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs: dict
) -> None:
    """Server, payload and network errors raise BoundariesError."""
    aioclient_mock.get(_url("ADM1"), **kwargs)
    with pytest.raises(BoundariesError):
        await async_download(async_get_clientsession(hass), "XXX")


def test_name_sort_key() -> None:
    """Ukrainian names follow the Ukrainian alphabet; Latin names still sort."""
    names = ["Івано-Франківська", "Автономна", "Київ", "Ґрунт", "Гола"]
    assert sorted(names, key=name_sort_key) == [
        "Автономна",
        "Гола",
        "Ґрунт",
        "Івано-Франківська",
        "Київ",
    ]
    assert sorted(["b", "A", "c"], key=name_sort_key) == ["A", "b", "c"]
