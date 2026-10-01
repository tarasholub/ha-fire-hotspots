"""Tests for the config, reauth and options flows."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.fire_hotspots.api import FirmsAuthError, FirmsConnectionError
from custom_components.fire_hotspots.boundaries import (
    BoundariesError,
    BoundariesNotFoundError,
)
from custom_components.fire_hotspots.const import (
    CONF_COUNTRY,
    CONF_HOURS,
    CONF_MAP_KEY,
    CONF_MIN_CONFIDENCE,
    CONF_REGIONS,
    CONF_SHOW_ON_MAP,
    CONF_SOURCES,
    DEFAULT_SOURCES,
    DOMAIN,
    WHOLE_COUNTRY,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

USER_INPUT = {CONF_MAP_KEY: " abc ", CONF_COUNTRY: "UA"}


@pytest.fixture
def mock_validate() -> Generator[AsyncMock]:
    """Patch the key validation request."""
    with patch(
        "custom_components.fire_hotspots.config_flow.FirmsClient.async_get_hotspots",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock:
        yield mock


@pytest.fixture
def mock_setup() -> Generator[AsyncMock]:
    """Skip entry setup after creation."""
    with patch(
        "custom_components.fire_hotspots.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def _through_download(hass: HomeAssistant, flow_id: str) -> dict:
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "download"
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(flow_id)


async def test_user_flow(
    hass: HomeAssistant,
    mock_validate: AsyncMock,
    mock_setup: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Key and country, download, regions; entry created with defaults."""
    hass.config.country = "UA"
    hass.config.language = "uk"
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await _through_download(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "regions"
    selector = result["data_schema"].schema[CONF_REGIONS].config
    values = [o["value"] for o in selector["options"]]
    assert values[0] == WHOLE_COUNTRY
    assert len(values) == 28
    labels = [o["label"] for o in selector["options"][1:]]
    assert labels[0] == "Автономна Республіка Крим"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REGIONS: []}
    )
    assert result["errors"] == {CONF_REGIONS: "no_regions"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REGIONS: ["UA-32", WHOLE_COUNTRY]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Україна"
    assert result["data"] == {CONF_MAP_KEY: "abc", CONF_COUNTRY: "UA"}
    assert result["options"][CONF_REGIONS] == ["UA-32", WHOLE_COUNTRY]
    assert result["options"][CONF_SOURCES] == DEFAULT_SOURCES
    assert result["options"][CONF_MIN_CONFIDENCE] == "low"
    assert result["result"].unique_id == "UA"
    assert mock_setup.called


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (FirmsAuthError, "invalid_auth"),
        (FirmsConnectionError, "cannot_connect"),
        (RuntimeError, "unknown"),
    ],
)
async def test_user_flow_key_errors(
    hass: HomeAssistant,
    mock_validate: AsyncMock,
    side_effect: type[Exception],
    error: str,
) -> None:
    """Key errors are shown on the first step."""
    mock_validate.side_effect = side_effect
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (BoundariesError, "boundaries_unavailable"),
        (BoundariesNotFoundError, "boundaries_not_found"),
    ],
)
async def test_user_flow_download_errors(
    hass: HomeAssistant,
    mock_validate: AsyncMock,
    side_effect: type[Exception],
    error: str,
) -> None:
    """Failed downloads return to the first step with an error."""
    with patch(
        "custom_components.fire_hotspots.config_flow.async_get_boundaries",
        side_effect=side_effect,
    ):
        result = await _start(hass)
        result = await _through_download(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


async def test_excluded_country(hass: HomeAssistant, mock_validate: AsyncMock) -> None:
    """Russia and Belarus are not selectable nor suggested."""
    hass.config.country = "RU"
    result = await _start(hass)
    selector = result["data_schema"].schema[CONF_COUNTRY].config
    assert "RU" not in selector["countries"]
    assert "BY" not in selector["countries"]
    assert "UA" in selector["countries"]
    for country in ("RU", "BY"):
        with pytest.raises(Exception, match="country"):
            await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_MAP_KEY: "abc", CONF_COUNTRY: country}
            )


async def test_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_validate: AsyncMock
) -> None:
    """One entry per country."""
    config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_validate: AsyncMock,
    mock_setup: AsyncMock,
) -> None:
    """Reauth stores the new key."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_validate.side_effect = FirmsAuthError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAP_KEY: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_validate.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAP_KEY: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_MAP_KEY] == "new"


async def test_options_flow(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_setup: AsyncMock,
    boundaries_cache: Path,
) -> None:
    """Options are validated and saved."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    options = {
        CONF_REGIONS: [],
        CONF_HOURS: 12,
        CONF_MIN_CONFIDENCE: "nominal",
        CONF_SOURCES: ["MODIS_NRT"],
        CONF_SHOW_ON_MAP: True,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options
    )
    assert result["errors"] == {CONF_REGIONS: "no_regions"}

    options[CONF_REGIONS] = ["UA-46"]
    options[CONF_SOURCES] = []
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options
    )
    assert result["errors"] == {CONF_SOURCES: "no_sources"}

    options[CONF_SOURCES] = ["MODIS_NRT"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_REGIONS] == ["UA-46"]
    assert config_entry.options[CONF_SHOW_ON_MAP] is True


async def test_options_flow_without_boundaries(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Options abort if boundaries cannot be loaded."""
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.fire_hotspots.config_flow.async_get_boundaries",
        side_effect=BoundariesError,
    ):
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "boundaries_unavailable"
