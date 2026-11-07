"""Unit tests for the departures coordinator."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.nsw_trains.api import Departure, InvalidApiKey, TransportNswError
from custom_components.nsw_trains.const import (
    CONF_INCLUDE_METRO,
    CONF_LATITUDE,
    CONF_LINE,
    CONF_LONGITUDE,
    CONF_PLATFORM,
    CONF_STATION_A,
    CONF_STATION_B,
    CONF_STOP_ID,
)
from custom_components.nsw_trains.coordinator import HomeCoordinator, LineCoordinator, NswTrainsCoordinator, filter_platform
from custom_components.nsw_trains.feeds import FeedCache
from custom_components.nsw_trains.gtfs import TripDelay, VehiclePosition

PLANNED = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)


def _departure(platform):
    return Departure("T1", "", "", platform, PLANNED, None, False, 1)


def make_coordinator(options=None, departures=None, error=None):
    entry = MagicMock()
    entry.title = "Central Station, Sydney"
    entry.data = {CONF_STOP_ID: "200060"}
    entry.options = options or {}
    client = MagicMock()
    client.async_get_departures = AsyncMock(return_value=departures or [], side_effect=error)
    return NswTrainsCoordinator(MagicMock(), entry, client), client


@pytest.mark.unit
async def test_fetches_departures_for_station():
    departures = [_departure("16")]
    coordinator, client = make_coordinator(departures=departures)

    assert await coordinator._async_update_data() == departures
    client.async_get_departures.assert_awaited_once_with("200060", include_metro=True)


@pytest.mark.unit
async def test_metro_option_is_passed_to_client():
    coordinator, client = make_coordinator(options={CONF_INCLUDE_METRO: False})

    await coordinator._async_update_data()

    client.async_get_departures.assert_awaited_once_with("200060", include_metro=False)


@pytest.mark.unit
async def test_platform_option_filters_departures():
    coordinator, _ = make_coordinator(options={CONF_PLATFORM: "16"}, departures=[_departure("16"), _departure("17")])

    assert [d.platform for d in await coordinator._async_update_data()] == ["16"]


@pytest.mark.unit
async def test_rejected_api_key_starts_reauth():
    coordinator, _ = make_coordinator(error=InvalidApiKey("nope"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.unit
async def test_api_errors_mark_update_failed():
    coordinator, _ = make_coordinator(error=TransportNswError("down"))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.unit
@pytest.mark.parametrize("platform", [None, ""])
def test_no_platform_keeps_everything(platform):
    departures = [_departure("16"), _departure(None)]
    assert filter_platform(departures, platform) == departures


@pytest.mark.unit
def test_platform_match_ignores_case_and_whitespace():
    departures = [_departure("1A"), _departure("1B"), _departure(None)]
    assert [d.platform for d in filter_platform(departures, " 1a ")] == ["1A"]


# --- Line and home coordinators ---

LINE_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _vehicle(trip_id, line="SCO", lat=-34.31, lon=150.925):
    return VehiclePosition(trip_id, line, f"Train {trip_id}", lat, lon, LINE_NOW)


def _client(**results):
    client = MagicMock()
    client.async_get_line_alerts = AsyncMock(return_value=results.get("alerts", []))
    client.async_get_vehicle_positions = AsyncMock(return_value=results.get("vehicles", []))
    client.async_get_trip_delays = AsyncMock(return_value=results.get("delays", {}))
    client.async_get_departures = AsyncMock(side_effect=results.get("departures"))
    for name, error in results.get("errors", {}).items():
        getattr(client, name).side_effect = error
    return client


def _entry(data):
    entry = MagicMock()
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return entry


@pytest.mark.unit
async def test_line_coordinator_computes_status():
    client = _client(
        vehicles=[_vehicle("a"), _vehicle("b")],
        delays={"a": TripDelay("a", "SCO", 0, False), "b": TripDelay("b", "SCO", 600, False)},
    )
    coordinator = LineCoordinator(MagicMock(), _entry({CONF_LINE: "SCO"}), client, FeedCache())

    status = await coordinator._async_update_data()

    assert status.status == "delays"
    assert status.on_time_percent == 50


@pytest.mark.unit
async def test_line_coordinators_share_feeds():
    """Two lines updating together fetch each network-wide feed once."""
    client, cache = _client(), FeedCache()
    sco = LineCoordinator(MagicMock(), _entry({CONF_LINE: "SCO"}), client, cache)
    t4 = LineCoordinator(MagicMock(), _entry({CONF_LINE: "T4"}), client, cache)

    await sco._async_update_data()
    await t4._async_update_data()

    assert client.async_get_line_alerts.await_count == 1
    assert client.async_get_vehicle_positions.await_count == 1
    assert client.async_get_trip_delays.await_count == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "error, expected", [(InvalidApiKey("bad"), ConfigEntryAuthFailed), (TransportNswError("down"), UpdateFailed)]
)
async def test_line_coordinator_errors(error, expected):
    client = _client(errors={"async_get_trip_delays": error})
    coordinator = LineCoordinator(MagicMock(), _entry({CONF_LINE: "SCO"}), client, FeedCache())

    with pytest.raises(expected):
        await coordinator._async_update_data()


AUSTINMER = {"id": "251540", "name": "Austinmer Station, Austinmer", "latitude": -34.306457, "longitude": 150.929233}
THIRROUL = {"id": "251550", "name": "Thirroul Station, Thirroul", "latitude": -34.318016, "longitude": 150.918912}
MIDPOINT = ((AUSTINMER["latitude"] + THIRROUL["latitude"]) / 2, (AUSTINMER["longitude"] + THIRROUL["longitude"]) / 2)


def _home_entry():
    return _entry(
        {CONF_LATITUDE: MIDPOINT[0], CONF_LONGITUDE: MIDPOINT[1], CONF_STATION_A: AUSTINMER, CONF_STATION_B: THIRROUL}
    )


def _dep(trip_id, minutes):
    return Departure("SCO", "", "Kiama", "1", LINE_NOW + timedelta(minutes=minutes), None, False, 1, (), trip_id)


@pytest.mark.unit
async def test_home_coordinator_predicts_passes_and_nearest_train():
    departures = {"251540": [_dep("south", 10)], "251550": [_dep("south", 14)]}
    client = _client(vehicles=[_vehicle("far", lat=-34.5, lon=150.8), _vehicle("near", lat=MIDPOINT[0], lon=MIDPOINT[1])])
    client.async_get_departures.side_effect = lambda stop_id, **kwargs: departures[stop_id]
    coordinator = HomeCoordinator(MagicMock(), _home_entry(), client, FeedCache())

    data = await coordinator._async_update_data()

    assert coordinator.fraction == pytest.approx(0.5)
    assert [(p.trip_id, p.towards) for p in data.passes] == [("south", "Thirroul Station, Thirroul")]
    assert data.passes[0].time == LINE_NOW + timedelta(minutes=12)
    assert data.nearest.vehicle.trip_id == "near"
    assert data.nearest.distance_m < 1


@pytest.mark.unit
async def test_home_coordinator_asks_for_departures_from_before_now():
    """A train between the stations has already left one of them; it must still be in that station's list."""
    client = _client(departures=lambda stop_id, **kwargs: [])
    coordinator = HomeCoordinator(MagicMock(), _home_entry(), client, FeedCache())

    await coordinator._async_update_data()

    for call in client.async_get_departures.await_args_list:
        assert call.kwargs["include_metro"] is False
        assert LINE_NOW - call.kwargs["now"] >= timedelta(minutes=14)


@pytest.mark.unit
async def test_home_coordinator_rejected_key():
    client = _client(errors={"async_get_departures": InvalidApiKey("bad")})
    coordinator = HomeCoordinator(MagicMock(), _home_entry(), client, FeedCache())

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
