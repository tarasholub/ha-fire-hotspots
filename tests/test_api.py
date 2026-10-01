"""Tests for the FIRMS client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.fire_hotspots.api import (
    API_BASE_URL,
    KEY_STATUS_URL,
    FirmsAuthError,
    FirmsClient,
    FirmsConnectionError,
    FirmsError,
    FirmsRateLimitError,
    normalize_confidence,
    parse_csv,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

# Real FIRMS responses (2026-10-01), trimmed.
VIIRS_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
52.53787,39.63092,317.67,0.69,0.74,2026-09-30,109,N20,VIIRS,n,2.0NRT,280.25,3.06,N
52.55538,39.57512,299.67,0.68,0.74,2026-09-30,1105,N20,VIIRS,h,2.0NRT,279.48,,D
bad,row,,,,,,,,,,,,
"""

MODIS_CSV = """latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight
52.53735,39.63926,313.74,1.05,1.02,2026-09-30,650,Terra,MODIS,74,6.1NRT,286.57,14.83,D
"""

BBOX = (30.0, 50.0, 31.0, 51.0)


def test_parse_viirs() -> None:
    """VIIRS rows are parsed, malformed rows skipped, time zero-padded."""
    hotspots = parse_csv("VIIRS_SNPP_NRT", VIIRS_CSV)
    assert len(hotspots) == 2
    first, second = hotspots
    assert first.acquired.isoformat() == "2026-09-30T01:09:00+00:00"
    assert first.satellite == "N20"
    assert first.confidence == "nominal"
    assert first.frp == 3.06
    assert second.confidence == "high"
    assert second.frp is None
    assert first.id == "VIIRS_SNPP_NRT_202609300109_52.5379_39.6309"


def test_parse_modis() -> None:
    """MODIS numeric confidence and unpadded time are handled."""
    (hotspot,) = parse_csv("MODIS_NRT", MODIS_CSV)
    assert hotspot.acquired.isoformat() == "2026-09-30T06:50:00+00:00"
    assert hotspot.satellite == "Terra"
    assert hotspot.confidence == "nominal"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("l", "low"),
        ("n", "nominal"),
        ("h", "high"),
        ("10", "low"),
        ("30", "nominal"),
        ("79", "nominal"),
        ("80", "high"),
        ("weird", "nominal"),
    ],
)
def test_normalize_confidence(raw: str, expected: str) -> None:
    """Confidence values map to low/nominal/high."""
    assert normalize_confidence(raw) == expected


def _url(day_range: int = 1) -> str:
    area = ",".join(f"{v:.4f}" for v in BBOX)
    return f"{API_BASE_URL}/key/VIIRS_SNPP_NRT/{area}/{day_range}"


async def test_client_ok(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """CSV response is parsed and day range is clamped to 5."""
    aioclient_mock.get(_url(5), text=VIIRS_CSV)
    client = FirmsClient(async_get_clientsession(hass), "key")
    hotspots = await client.async_get_hotspots("VIIRS_SNPP_NRT", BBOX, 9)
    assert len(hotspots) == 2


async def test_client_header_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No detections returns an empty list."""
    aioclient_mock.get(_url(), text=VIIRS_CSV.splitlines()[0])
    client = FirmsClient(async_get_clientsession(hass), "key")
    assert await client.async_get_hotspots("VIIRS_SNPP_NRT", BBOX) == []


@pytest.mark.parametrize(
    ("status", "text", "error"),
    [
        (400, "Invalid MAP_KEY.", FirmsAuthError),  # real FIRMS response
        (403, "Exceeding allowed transaction limit.", FirmsRateLimitError),
        (503, "Service Unavailable", FirmsConnectionError),
        (400, "Invalid area coordinates.", FirmsError),
    ],
)
async def test_client_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    text: str,
    error: type[Exception],
) -> None:
    """Error responses map to typed exceptions."""
    aioclient_mock.get(_url(), status=status, text=text)
    client = FirmsClient(async_get_clientsession(hass), "key")
    with pytest.raises(error):
        await client.async_get_hotspots("VIIRS_SNPP_NRT", BBOX)


async def test_client_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Network errors raise FirmsConnectionError."""
    aioclient_mock.get(_url(), exc=TimeoutError())
    client = FirmsClient(async_get_clientsession(hass), "key")
    with pytest.raises(FirmsConnectionError):
        await client.async_get_hotspots("VIIRS_SNPP_NRT", BBOX)


async def test_key_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Key status endpoint is parsed; malformed payloads raise FirmsError."""
    aioclient_mock.get(
        KEY_STATUS_URL,
        json={
            "transaction_limit": 5000,
            "current_transactions": 42,
            "transaction_interval": "10 minutes",
        },
    )
    client = FirmsClient(async_get_clientsession(hass), "key")
    status = await client.async_get_key_status()
    assert (status.used, status.limit, status.interval) == (42, 5000, "10 minutes")

    aioclient_mock.clear_requests()
    aioclient_mock.get(KEY_STATUS_URL, json={"unexpected": True})
    with pytest.raises(FirmsError):
        await client.async_get_key_status()
