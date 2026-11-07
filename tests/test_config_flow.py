"""Unit tests for the config, reauth and options flows."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import AbortFlow

from custom_components.nsw_trains import async_setup_entry
from custom_components.nsw_trains.api import InvalidApiKey, Stop, TransportNswError
from custom_components.nsw_trains.config_flow import NswTrainsConfigFlow
from custom_components.nsw_trains.const import (
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
    ENTRY_TYPE_HOME,
    ENTRY_TYPE_LINE,
    ENTRY_TYPE_STATION,
)

ENTRY_ID = "entry-1"
STOPS = [Stop("200060", "Central Station, Sydney"), Stop("209110", "Central Station Metro, Sydney")]


def make_flow(stops=None, error=None, current_entries=()):
    flow = NswTrainsConfigFlow()
    flow.hass = MagicMock()
    flow.current_entries = list(current_entries)
    client = MagicMock()
    client.async_find_stops = AsyncMock(return_value=stops if stops is not None else STOPS, side_effect=error)
    patcher = patch("custom_components.nsw_trains.config_flow.TransportNswClient", return_value=client)
    patcher.start()
    return flow, client, patcher


@pytest.fixture(autouse=True)
def _stop_patches():
    yield
    patch.stopall()


def make_entry(data=None, options=None, unique_id="200060"):
    entry = MagicMock()
    entry.entry_id = ENTRY_ID
    entry.unique_id = unique_id
    entry.data = data or {CONF_API_KEY: "old", CONF_STOP_ID: unique_id, CONF_STOP_NAME: "Central"}
    entry.options = options or {}
    return entry


def form_defaults(result):
    return {str(key): key.default() for key in result["data_schema"].schema if callable(getattr(key, "default", None))}


# --- user / station steps ---


@pytest.mark.unit
async def test_user_step_shows_menu():
    flow, _, _ = make_flow()

    result = await flow.async_step_user()

    assert result["type"] == "menu"
    assert result["menu_options"] == ["station_search", "line", "home"]


@pytest.mark.unit
async def test_station_search_shows_form():
    flow, _, _ = make_flow()

    result = await flow.async_step_station_search()

    assert result["type"] == "form"
    assert result["step_id"] == "station_search"


@pytest.mark.unit
async def test_user_step_reuses_existing_api_key():
    flow, _, _ = make_flow(current_entries=[make_entry()])

    result = await flow.async_step_station_search()

    assert form_defaults(result)[CONF_API_KEY] == "old"


@pytest.mark.unit
async def test_full_flow_creates_entry():
    flow, client, _ = make_flow()

    result = await flow.async_step_station_search({CONF_API_KEY: " key ", CONF_SEARCH: " Central "})
    assert result["type"] == "form"
    assert result["step_id"] == "station"
    client.async_find_stops.assert_awaited_once_with("Central")

    result = await flow.async_step_station({CONF_STOP_ID: "200060"})

    assert result["type"] == "create_entry"
    assert result["title"] == "Central Station, Sydney"
    assert result["data"] == {
        CONF_ENTRY_TYPE: ENTRY_TYPE_STATION,
        CONF_API_KEY: "key",
        CONF_STOP_ID: "200060",
        CONF_STOP_NAME: "Central Station, Sydney",
    }
    assert flow.unique_id == "200060"


@pytest.mark.unit
async def test_station_already_configured():
    flow, _, _ = make_flow(current_entries=[make_entry()])
    await flow.async_step_station_search({CONF_API_KEY: "key", CONF_SEARCH: "Central"})

    with pytest.raises(AbortFlow):
        await flow.async_step_station({CONF_STOP_ID: "200060"})


@pytest.mark.unit
@pytest.mark.parametrize(
    "error, stops, field, code",
    [
        (InvalidApiKey("bad"), None, CONF_API_KEY, "invalid_auth"),
        (TransportNswError("down"), None, "base", "cannot_connect"),
        (None, [], CONF_SEARCH, "no_stations"),
    ],
)
async def test_user_step_errors(error, stops, field, code):
    flow, _, _ = make_flow(stops=stops, error=error)

    result = await flow.async_step_station_search({CONF_API_KEY: "key", CONF_SEARCH: "Nowhere"})

    assert result["type"] == "form"
    assert result["step_id"] == "station_search"
    assert result["errors"] == {field: code}
    # Keep what the user typed
    assert form_defaults(result)[CONF_SEARCH] == "Nowhere"


# --- reauth ---


@pytest.mark.unit
async def test_reauth_updates_api_key():
    flow, client, _ = make_flow()
    entry = make_entry()
    flow.reauth_entry = entry

    result = await flow.async_step_reauth(entry.data)
    assert result["step_id"] == "reauth_confirm"

    result = await flow.async_step_reauth_confirm({CONF_API_KEY: "new"})

    assert result == {"type": "abort", "reason": "reauth_successful"}
    assert entry.data[CONF_API_KEY] == "new"
    assert entry.data[CONF_STOP_ID] == "200060"


@pytest.mark.unit
async def test_reauth_rejects_bad_key():
    flow, _, _ = make_flow(error=InvalidApiKey("bad"))
    flow.reauth_entry = make_entry()

    result = await flow.async_step_reauth_confirm({CONF_API_KEY: "still bad"})

    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}
    assert flow.reauth_entry.data[CONF_API_KEY] == "old"


# --- options ---


def make_options_flow(entry):
    flow = NswTrainsConfigFlow.async_get_options_flow(entry)
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_known_entry.return_value = entry
    flow.handler = ENTRY_ID
    return flow


@pytest.mark.unit
def test_get_options_flow_returns_options_flow():
    assert isinstance(NswTrainsConfigFlow.async_get_options_flow(make_entry()), config_entries.OptionsFlow)


@pytest.mark.unit
async def test_options_defaults():
    result = await make_options_flow(make_entry()).async_step_init()

    assert result["step_id"] == "station"
    assert form_defaults(result) == {CONF_DEPARTURE_COUNT: 3, CONF_INCLUDE_METRO: True, CONF_PLATFORM: ""}


@pytest.mark.unit
async def test_options_defaults_to_current_values():
    entry = make_entry(options={CONF_DEPARTURE_COUNT: 5, CONF_INCLUDE_METRO: False, CONF_PLATFORM: "16"})

    result = await make_options_flow(entry).async_step_init()

    assert form_defaults(result) == {CONF_DEPARTURE_COUNT: 5, CONF_INCLUDE_METRO: False, CONF_PLATFORM: "16"}


@pytest.mark.unit
async def test_options_saved_with_trimmed_platform():
    result = await make_options_flow(make_entry()).async_step_init(
        {CONF_DEPARTURE_COUNT: 2, CONF_INCLUDE_METRO: False, CONF_PLATFORM: " 16 "}
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_DEPARTURE_COUNT: 2, CONF_INCLUDE_METRO: False, CONF_PLATFORM: "16"}


# --- setup ---


@pytest.mark.unit
async def test_setup_entry_reloads_when_options_change():
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock(return_value=True)
    entry = make_entry()

    coordinator_cls = MagicMock()
    coordinator_cls.return_value.async_config_entry_first_refresh = AsyncMock()
    with patch.dict("custom_components.nsw_trains.COORDINATORS", {ENTRY_TYPE_STATION: coordinator_cls}):
        assert await async_setup_entry(hass, entry)

    assert entry.runtime_data is coordinator_cls.return_value
    listener = entry.add_update_listener.call_args.args[0]
    await listener(hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with(ENTRY_ID)


# --- Line status ---


@pytest.mark.unit
async def test_line_flow_creates_entry():
    flow, client, _ = make_flow()

    form = await flow.async_step_line()
    assert form["step_id"] == "line"

    result = await flow.async_step_line({CONF_API_KEY: " key ", CONF_LINE: "SCO"})

    assert result["type"] == "create_entry"
    assert result["title"] == "South Coast Line"
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_LINE, CONF_API_KEY: "key", CONF_LINE: "SCO"}
    assert flow.unique_id == "line_SCO"


@pytest.mark.unit
async def test_line_flow_rejects_bad_key():
    flow, _, _ = make_flow(error=InvalidApiKey("bad"))

    result = await flow.async_step_line({CONF_API_KEY: "bad", CONF_LINE: "SCO"})

    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


@pytest.mark.unit
async def test_line_already_configured():
    flow, _, _ = make_flow(current_entries=[make_entry(unique_id="line_SCO")])

    with pytest.raises(AbortFlow):
        await flow.async_step_line({CONF_API_KEY: "key", CONF_LINE: "SCO"})


# --- Home ---

NEARBY = [
    Stop("251540", "Austinmer Station, Austinmer", -34.306457, 150.929233),
    Stop("251550", "Thirroul Station, Thirroul", -34.318016, 150.918912),
    Stop("251530", "Coledale Station, Coledale", -34.289673, 150.94316),
]


def make_home_flow(stations=NEARBY, error=None, current_entries=()):
    flow, client, patcher = make_flow(current_entries=current_entries)
    flow.hass.config.latitude = -34.31
    flow.hass.config.longitude = 150.925
    client.async_find_stations_near = AsyncMock(return_value=stations, side_effect=error)
    return flow, client


@pytest.mark.unit
async def test_home_form_defaults_to_home_location():
    flow, _ = make_home_flow()

    result = await flow.async_step_home()

    defaults = form_defaults(result)
    assert (defaults[CONF_LATITUDE], defaults[CONF_LONGITUDE]) == (-34.31, 150.925)


@pytest.mark.unit
async def test_home_flow_suggests_nearest_stations_and_creates_entry():
    flow, client = make_home_flow()

    result = await flow.async_step_home({CONF_API_KEY: "key", CONF_LATITUDE: -34.31, CONF_LONGITUDE: 150.925})

    client.async_find_stations_near.assert_awaited_once_with(-34.31, 150.925, 8000)
    assert result["step_id"] == "home_stations"
    assert form_defaults(result) == {CONF_STATION_A: "251540", CONF_STATION_B: "251550"}

    result = await flow.async_step_home_stations({CONF_STATION_A: "251540", CONF_STATION_B: "251550"})

    assert result["type"] == "create_entry"
    assert result["title"] == "Home"
    assert result["data"] == {
        CONF_ENTRY_TYPE: ENTRY_TYPE_HOME,
        CONF_API_KEY: "key",
        CONF_LATITUDE: -34.31,
        CONF_LONGITUDE: 150.925,
        CONF_STATION_A: {
            "id": "251540",
            "name": "Austinmer Station, Austinmer",
            "latitude": -34.306457,
            "longitude": 150.929233,
        },
        CONF_STATION_B: {
            "id": "251550",
            "name": "Thirroul Station, Thirroul",
            "latitude": -34.318016,
            "longitude": 150.918912,
        },
    }
    assert flow.unique_id == "home_251540_251550"


@pytest.mark.unit
async def test_home_unique_id_ignores_station_order():
    flow, _ = make_home_flow()
    await flow.async_step_home({CONF_API_KEY: "key", CONF_LATITUDE: -34.31, CONF_LONGITUDE: 150.925})

    await flow.async_step_home_stations({CONF_STATION_A: "251550", CONF_STATION_B: "251540"})

    assert flow.unique_id == "home_251540_251550"


@pytest.mark.unit
async def test_home_stations_must_differ():
    flow, _ = make_home_flow()
    await flow.async_step_home({CONF_API_KEY: "key", CONF_LATITUDE: -34.31, CONF_LONGITUDE: 150.925})

    result = await flow.async_step_home_stations({CONF_STATION_A: "251540", CONF_STATION_B: "251540"})

    assert result["errors"] == {"base": "same_station"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "stations, error, errors",
    [
        (NEARBY[:1], None, {"base": "no_nearby_stations"}),
        ([], None, {"base": "no_nearby_stations"}),
        (None, InvalidApiKey("bad"), {CONF_API_KEY: "invalid_auth"}),
        (None, TransportNswError("down"), {"base": "cannot_connect"}),
    ],
)
async def test_home_errors(stations, error, errors):
    flow, _ = make_home_flow(stations=stations, error=error)

    result = await flow.async_step_home({CONF_API_KEY: "key", CONF_LATITUDE: -34.31, CONF_LONGITUDE: 150.925})

    assert result["step_id"] == "home"
    assert result["errors"] == errors


# --- Options by entry type ---


@pytest.mark.unit
@pytest.mark.parametrize(
    "entry_type, supported", [(ENTRY_TYPE_STATION, True), (None, True), (ENTRY_TYPE_HOME, True), (ENTRY_TYPE_LINE, False)]
)
def test_options_supported_by_entry_type(entry_type, supported):
    entry = make_entry(data={CONF_ENTRY_TYPE: entry_type} if entry_type else None)
    assert NswTrainsConfigFlow.async_supports_options_flow(entry) is supported


@pytest.mark.unit
async def test_home_options():
    entry = make_entry(data={CONF_ENTRY_TYPE: ENTRY_TYPE_HOME})
    flow = make_options_flow(entry)

    result = await flow.async_step_init()
    assert result["step_id"] == "home"
    assert form_defaults(result) == {CONF_PASSING_RADIUS: 300}

    result = await flow.async_step_init({CONF_PASSING_RADIUS: 500})
    assert result["data"] == {CONF_PASSING_RADIUS: 500}


@pytest.mark.unit
@pytest.mark.parametrize("entry_type", [ENTRY_TYPE_LINE, ENTRY_TYPE_HOME])
async def test_setup_entry_uses_coordinator_for_type(entry_type):
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    entry = make_entry(data={CONF_ENTRY_TYPE: entry_type, CONF_API_KEY: "key"})
    coordinator_cls = MagicMock()
    coordinator_cls.return_value.async_config_entry_first_refresh = AsyncMock()

    with patch.dict("custom_components.nsw_trains.COORDINATORS", {entry_type: coordinator_cls}):
        assert await async_setup_entry(hass, entry)

    assert entry.runtime_data is coordinator_cls.return_value
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(entry, ["sensor", "binary_sensor"])


@pytest.mark.unit
@pytest.mark.parametrize("step", ["async_step_station_search", "async_step_line"])
async def test_forms_link_to_open_data_hub(step):
    flow, _, _ = make_flow()

    result = await getattr(flow, step)()

    assert result["description_placeholders"] == {"docs_url": "https://opendata.transport.nsw.gov.au/"}
