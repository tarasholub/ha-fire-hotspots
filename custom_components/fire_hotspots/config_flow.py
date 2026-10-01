"""Config flow for Fire Hotspots: key and country, boundaries, regions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    CountrySelector,
    CountrySelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import FirmsAuthError, FirmsClient, FirmsError
from .boundaries import (
    BoundariesError,
    BoundariesNotFoundError,
    CountryBoundaries,
    name_sort_key,
)
from .const import (
    CONF_COUNTRY,
    CONF_HOURS,
    CONF_MAP_KEY,
    CONF_MIN_CONFIDENCE,
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
    MAP_KEY_URL,
    MAX_HOURS,
    MAX_UPDATE_MINUTES,
    MAX_ZONE_RADIUS_KM,
    MIN_HOURS,
    MIN_UPDATE_MINUTES,
    MIN_ZONE_RADIUS_KM,
    SOURCE_LABELS,
    SOURCE_VIIRS_SNPP,
    WHOLE_COUNTRY,
)
from .countries import COUNTRIES, EXCLUDED_COUNTRIES, country_name
from .helpers import async_get_boundaries

if TYPE_CHECKING:
    import asyncio

    from homeassistant.core import HomeAssistant

    from .data import FirmsConfigEntry

ALLOWED_COUNTRIES = sorted(set(COUNTRIES) - EXCLUDED_COUNTRIES)
MAP_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
VALIDATION_BBOX = (30.0, 50.0, 30.01, 50.01)  # tiny area: one cheap request


async def validate_map_key(hass: HomeAssistant, map_key: str) -> str | None:
    """Make one cheap request. Return an error key or None."""
    client = FirmsClient(async_get_clientsession(hass), map_key)
    try:
        await client.async_get_hotspots(SOURCE_VIIRS_SNPP, VALIDATION_BBOX, 1)
    except FirmsAuthError:
        return "invalid_auth"
    except FirmsError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        LOGGER.exception("Unexpected error validating MAP_KEY")
        return "unknown"
    return None


def normalize_regions(selected: list[str], boundaries: CountryBoundaries) -> list[str]:
    """
    Keep the group selection consistent: whole country ⟺ all regions.

    Picking the whole country expands to every region; picking every region
    adds the whole country. A partial selection stays as is.
    """
    all_regions = sorted(boundaries.regions) if boundaries.level != "ADM0" else []
    chosen = set(selected)
    if WHOLE_COUNTRY in chosen or (all_regions and chosen >= set(all_regions)):
        return [WHOLE_COUNTRY, *all_regions]
    return selected


def regions_selector(boundaries: CountryBoundaries, language: str) -> SelectSelector:
    """Checkbox list: whole country first, then regions by localized name."""
    regions = sorted(
        boundaries.regions.values(), key=lambda r: name_sort_key(r.name(language))
    )
    options = [SelectOptionDict(value=WHOLE_COUNTRY, label=WHOLE_COUNTRY)]
    if boundaries.level != "ADM0":
        options += [
            SelectOptionDict(value=r.id, label=r.name(language)) for r in regions
        ]
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=SelectSelectorMode.LIST,
            translation_key=CONF_REGIONS,
        )
    )


class FireHotspotsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a country: MAP_KEY and country, download boundaries, pick regions."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._map_key = ""
        self._country = ""
        self._boundaries: CountryBoundaries | None = None
        self._download: asyncio.Task[CountryBoundaries] | None = None
        self._download_error: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for MAP_KEY and country."""
        errors: dict[str, str] = {}
        if self._download_error:
            errors["base"] = self._download_error
            self._download_error = None
        elif user_input is not None:
            country = user_input[CONF_COUNTRY]
            await self.async_set_unique_id(country)
            self._abort_if_unique_id_configured()
            map_key = user_input[CONF_MAP_KEY].strip()
            if error := await validate_map_key(self.hass, map_key):
                errors["base"] = error
            else:
                self._map_key, self._country = map_key, country
                return await self.async_step_boundaries()

        home = self.hass.config.country
        suggested = user_input or {
            CONF_COUNTRY: home if home in ALLOWED_COUNTRIES else None
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_MAP_KEY): MAP_KEY_SELECTOR,
                vol.Required(CONF_COUNTRY): CountrySelector(
                    CountrySelectorConfig(countries=ALLOWED_COUNTRIES)
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={"map_key_url": MAP_KEY_URL},
        )

    async def async_step_boundaries(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Download country boundaries with a progress indicator."""
        del user_input
        if self._download is None:
            self._download = self.hass.async_create_task(
                async_get_boundaries(
                    self.hass, self._country, self.async_update_progress
                ),
                eager_start=False,
            )
        if not self._download.done():
            return self.async_show_progress(
                step_id="boundaries",
                progress_action="download",
                progress_task=self._download,
                description_placeholders={
                    "country": country_name(self._country, self.hass.config.language)
                },
            )

        task, self._download = self._download, None
        try:
            self._boundaries = task.result()
        except BoundariesNotFoundError:
            self._download_error = "boundaries_not_found"
        except BoundariesError:
            self._download_error = "boundaries_unavailable"
        if self._download_error:
            return self.async_show_progress_done(next_step_id="user")
        return self.async_show_progress_done(next_step_id="regions")

    async def async_step_regions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick regions to monitor."""
        assert self._boundaries is not None  # noqa: S101
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_REGIONS):
                return self.async_create_entry(
                    title=country_name(self._country, self.hass.config.language),
                    data={CONF_MAP_KEY: self._map_key, CONF_COUNTRY: self._country},
                    options={
                        CONF_REGIONS: normalize_regions(
                            user_input[CONF_REGIONS], self._boundaries
                        ),
                        CONF_HOURS: DEFAULT_HOURS,
                        CONF_MIN_CONFIDENCE: DEFAULT_MIN_CONFIDENCE,
                        CONF_SOURCES: DEFAULT_SOURCES,
                        CONF_SHOW_ON_MAP: DEFAULT_SHOW_ON_MAP,
                        CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_MINUTES,
                    },
                )
            errors[CONF_REGIONS] = "no_regions"

        schema = vol.Schema(
            {
                vol.Required(CONF_REGIONS): regions_selector(
                    self._boundaries, self.hass.config.language
                )
            }
        )
        return self.async_show_form(
            step_id="regions",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "country": country_name(self._country, self.hass.config.language)
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after FIRMS rejected the MAP_KEY."""
        del entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new MAP_KEY."""
        errors: dict[str, str] = {}
        if user_input is not None:
            map_key = user_input[CONF_MAP_KEY].strip()
            if error := await validate_map_key(self.hass, map_key):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_MAP_KEY: map_key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_MAP_KEY): MAP_KEY_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: FirmsConfigEntry,
    ) -> FireHotspotsOptionsFlow:
        """Return the options flow."""
        del config_entry
        return FireHotspotsOptionsFlow()


class FireHotspotsOptionsFlow(OptionsFlowWithReload):
    """Regions, time window, confidence, sources and map markers."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        try:
            boundaries = await async_get_boundaries(
                self.hass, self.config_entry.data[CONF_COUNTRY]
            )
        except BoundariesError:
            return self.async_abort(reason="boundaries_unavailable")

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_REGIONS):
                errors[CONF_REGIONS] = "no_regions"
            elif not user_input.get(CONF_SOURCES):
                errors[CONF_SOURCES] = "no_sources"
            else:
                user_input[CONF_REGIONS] = normalize_regions(
                    user_input[CONF_REGIONS], boundaries
                )
                return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_REGIONS): regions_selector(
                    boundaries, self.hass.config.language
                ),
                vol.Required(CONF_HOURS): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_HOURS,
                        max=MAX_HOURS,
                        step=1,
                        unit_of_measurement="h",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_MIN_CONFIDENCE): SelectSelector(
                    SelectSelectorConfig(
                        options=CONFIDENCE_LEVELS,
                        translation_key=CONF_MIN_CONFIDENCE,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_SOURCES): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=source, label=label)
                            for source, label in SOURCE_LABELS.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_MINUTES
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_UPDATE_MINUTES,
                        max=MAX_UPDATE_MINUTES,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_ZONES, default=[]): EntitySelector(
                    EntitySelectorConfig(domain="zone", multiple=True)
                ),
                vol.Required(
                    CONF_ZONE_RADIUS, default=DEFAULT_ZONE_RADIUS_KM
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_ZONE_RADIUS_KM,
                        max=MAX_ZONE_RADIUS_KM,
                        step=1,
                        unit_of_measurement="km",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_SHOW_ON_MAP): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or dict(self.config_entry.options)
            ),
            errors=errors,
        )
