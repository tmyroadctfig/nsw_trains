import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InvalidApiKey, Stop, TransportNswClient, TransportNswError
from .const import (
    CONF_API_KEY,
    CONF_DEPARTURE_COUNT,
    CONF_ENTRY_TYPE,
    CONF_INCLUDE_METRO,
    CONF_LATITUDE,
    CONF_LINE,
    CONF_LONGITUDE,
    CONF_PASSING_RADIUS,
    CONF_PLATFORM,
    CONF_SEARCH,
    CONF_STATION_A,
    CONF_STATION_B,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_INCLUDE_METRO,
    DEFAULT_PASSING_RADIUS,
    DOMAIN,
    ENTRY_TYPE_HOME,
    ENTRY_TYPE_LINE,
    ENTRY_TYPE_STATION,
    LINES,
    MAX_DEPARTURE_COUNT,
    NEARBY_STATION_RADIUS,
)
from .coordinator import stop_to_data

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

# Any well-known station works for checking that an API key is accepted
API_KEY_CHECK_QUERY = "Central"

HOME_TITLE = "Home"


class NswTrainsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._stops: list[Stop] = []
        self._home: tuple[float, float] | None = None

    def _client(self, api_key: str) -> TransportNswClient:
        return TransportNswClient(async_get_clientsession(self.hass), api_key)

    def _existing_api_key(self) -> str | None:
        """Reuse the API key from an already configured entry so it only has to be entered once."""
        for entry in self._async_current_entries():
            api_key: str | None = entry.data.get(CONF_API_KEY)
            if api_key:
                return api_key
        return None

    def _api_key_field(self, defaults: dict[str, Any]) -> dict[Any, Any]:
        return {vol.Required(CONF_API_KEY, default=defaults.get(CONF_API_KEY, self._existing_api_key() or "")): str}

    @staticmethod
    async def _call(fetch: Callable[[], Awaitable[T]], errors: dict[str, str]) -> T | None:
        """Run an API call, recording a form error instead of raising."""
        try:
            return await fetch()
        except InvalidApiKey:
            errors[CONF_API_KEY] = "invalid_auth"
        except TransportNswError:
            errors["base"] = "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        """Choose what to add."""
        return self.async_show_menu(step_id="user", menu_options=["station_search", "line", "home"])

    # --- Station departures ---

    async def async_step_station_search(self, user_input: dict[str, Any] | None = None) -> Any:
        """Ask for the API key and a station to search for."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            client = self._client(api_key)
            stops = await self._call(lambda: client.async_find_stops(user_input[CONF_SEARCH].strip()), errors)
            if stops is not None and not stops:
                errors[CONF_SEARCH] = "no_stations"
            elif stops:
                self._api_key = api_key
                self._stops = stops
                return await self.async_step_station()

        defaults = user_input or {}
        schema = vol.Schema(
            {**self._api_key_field(defaults), vol.Required(CONF_SEARCH, default=defaults.get(CONF_SEARCH, "")): str}
        )
        return self.async_show_form(step_id="station_search", data_schema=schema, errors=errors)

    async def async_step_station(self, user_input: dict[str, Any] | None = None) -> Any:
        """Pick one of the stations matching the search."""
        stops_by_id = {stop.id: stop.name for stop in self._stops}

        if user_input is not None:
            stop_id = user_input[CONF_STOP_ID]
            await self.async_set_unique_id(stop_id)
            self._abort_if_unique_id_configured()

            stop_name = stops_by_id[stop_id]
            _LOGGER.info("Setting up station %s: %s", stop_id, stop_name)
            return self.async_create_entry(
                title=stop_name,
                data={
                    CONF_ENTRY_TYPE: ENTRY_TYPE_STATION,
                    CONF_API_KEY: self._api_key,
                    CONF_STOP_ID: stop_id,
                    CONF_STOP_NAME: stop_name,
                },
            )

        schema = vol.Schema({vol.Required(CONF_STOP_ID): vol.In(stops_by_id)})
        return self.async_show_form(step_id="station", data_schema=schema)

    # --- Line status ---

    async def async_step_line(self, user_input: dict[str, Any] | None = None) -> Any:
        """Ask for the API key and which line to follow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            client = self._client(api_key)
            if await self._call(lambda: client.async_find_stops(API_KEY_CHECK_QUERY), errors) is not None:
                line = user_input[CONF_LINE]
                await self.async_set_unique_id(f"line_{line}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=LINES[line], data={CONF_ENTRY_TYPE: ENTRY_TYPE_LINE, CONF_API_KEY: api_key, CONF_LINE: line}
                )

        defaults = user_input or {}
        schema = vol.Schema(
            {**self._api_key_field(defaults), vol.Required(CONF_LINE, default=defaults.get(CONF_LINE, "T1")): vol.In(LINES)}
        )
        return self.async_show_form(step_id="line", data_schema=schema, errors=errors)

    # --- Trains passing home ---

    async def async_step_home(self, user_input: dict[str, Any] | None = None) -> Any:
        """Ask for the API key and the location to watch, defaulting to Home Assistant's home location."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            latitude, longitude = float(user_input[CONF_LATITUDE]), float(user_input[CONF_LONGITUDE])
            client = self._client(api_key)
            stations = await self._call(
                lambda: client.async_find_stations_near(latitude, longitude, NEARBY_STATION_RADIUS), errors
            )
            if stations is not None and len(stations) < 2:
                errors["base"] = "no_nearby_stations"
            elif stations:
                self._api_key = api_key
                self._home = (latitude, longitude)
                self._stops = stations
                return await self.async_step_home_stations()

        defaults = user_input or {}
        schema = vol.Schema(
            {
                **self._api_key_field(defaults),
                vol.Required(CONF_LATITUDE, default=defaults.get(CONF_LATITUDE, self.hass.config.latitude)): vol.Coerce(float),
                vol.Required(CONF_LONGITUDE, default=defaults.get(CONF_LONGITUDE, self.hass.config.longitude)): vol.Coerce(
                    float
                ),
            }
        )
        return self.async_show_form(step_id="home", data_schema=schema, errors=errors)

    async def async_step_home_stations(self, user_input: dict[str, Any] | None = None) -> Any:
        """Confirm the stations either side of the location. The nearest two are suggested."""
        errors: dict[str, str] = {}
        stations = {stop.id: stop for stop in self._stops}

        if user_input is not None:
            station_a, station_b = stations[user_input[CONF_STATION_A]], stations[user_input[CONF_STATION_B]]
            if station_a.id == station_b.id:
                errors["base"] = "same_station"
            else:
                await self.async_set_unique_id(f"home_{'_'.join(sorted((station_a.id, station_b.id)))}")
                self._abort_if_unique_id_configured()
                latitude, longitude = self._home or (0.0, 0.0)
                return self.async_create_entry(
                    title=HOME_TITLE,
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_HOME,
                        CONF_API_KEY: self._api_key,
                        CONF_LATITUDE: latitude,
                        CONF_LONGITUDE: longitude,
                        CONF_STATION_A: stop_to_data(station_a),
                        CONF_STATION_B: stop_to_data(station_b),
                    },
                )

        names = {stop.id: stop.name for stop in self._stops}
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_A, default=self._stops[0].id): vol.In(names),
                vol.Required(CONF_STATION_B, default=self._stops[1].id): vol.In(names),
            }
        )
        return self.async_show_form(step_id="home_stations", data_schema=schema, errors=errors)

    # --- Reauth ---

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> Any:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> Any:
        """Ask for a new API key after the old one was rejected."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            client = self._client(api_key)
            if await self._call(lambda: client.async_find_stops(API_KEY_CHECK_QUERY), errors) is not None:
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates={CONF_API_KEY: api_key})

        schema = vol.Schema({vol.Required(CONF_API_KEY): str})
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "NswTrainsOptionsFlow":
        return NswTrainsOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: config_entries.ConfigEntry) -> bool:
        """Lines have nothing to configure."""
        return bool(config_entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_STATION) != ENTRY_TYPE_LINE)


class NswTrainsOptionsFlow(config_entries.OptionsFlow):
    """Station: how many departures and which ones. Home: how close a train must be to count as passing."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        if self.config_entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HOME:
            return await self.async_step_home(user_input)
        return await self.async_step_station(user_input)

    async def async_step_station(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            user_input[CONF_PLATFORM] = (user_input.get(CONF_PLATFORM) or "").strip()
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEPARTURE_COUNT, default=options.get(CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_DEPARTURE_COUNT)),
                vol.Required(CONF_INCLUDE_METRO, default=options.get(CONF_INCLUDE_METRO, DEFAULT_INCLUDE_METRO)): bool,
                vol.Optional(CONF_PLATFORM, default=options.get(CONF_PLATFORM, "")): str,
            }
        )
        return self.async_show_form(step_id="station", data_schema=schema)

    async def async_step_home(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_PASSING_RADIUS, default=options.get(CONF_PASSING_RADIUS, DEFAULT_PASSING_RADIUS)): vol.All(
                    vol.Coerce(int), vol.Range(min=50, max=5000)
                ),
            }
        )
        return self.async_show_form(step_id="home", data_schema=schema)
