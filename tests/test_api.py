"""Unit tests for parsing Trip Planner API responses and the API client."""

from datetime import datetime, timezone

import aiohttp
import pytest

from custom_components.nsw_trains.api import (
    DEPARTURE_MON_ENDPOINT,
    STOP_FINDER_ENDPOINT,
    Departure,
    InvalidApiKey,
    TransportNswClient,
    TransportNswError,
    parse_departures,
    parse_stops,
)
from custom_components.nsw_trains.const import PRODUCT_CLASS_TRAIN

from .helpers import FakeResponse, FakeSession


def _utc(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 7, hour, minute, second, tzinfo=timezone.utc)


# --- parse_departures ---


@pytest.mark.unit
def test_departures_sorted_soonest_first(departures_json):
    lines = [departure.line for departure in parse_departures(departures_json)]
    assert lines == ["T1", "M1", "T8", "T4"]


@pytest.mark.unit
def test_departures_exclude_cancelled_and_non_rail(departures_json):
    departures = parse_departures(departures_json)
    assert "Richmond" not in [d.destination for d in departures]
    assert "L2" not in [d.line for d in departures]


@pytest.mark.unit
def test_departures_can_exclude_metro(departures_json):
    departures = parse_departures(departures_json, frozenset({PRODUCT_CLASS_TRAIN}))
    assert [d.line for d in departures] == ["T1", "T8", "T4"]


@pytest.mark.unit
def test_departure_fields(departures_json):
    t8 = next(d for d in parse_departures(departures_json) if d.line == "T8")

    assert t8.line_name == "T8 Airport & South Line"
    assert t8.destination == "Macarthur"
    assert t8.platform == "18"
    assert t8.planned == _utc(3, 10)
    assert t8.estimated == _utc(3, 12)
    assert t8.realtime is True
    assert t8.product_class == PRODUCT_CLASS_TRAIN
    assert [alert.id for alert in t8.alerts] == ["alert-lift"]


@pytest.mark.unit
def test_departure_without_estimate_uses_timetable(departures_json):
    t4 = next(d for d in parse_departures(departures_json) if d.line == "T4")

    assert t4.estimated is None
    assert t4.departure_time == t4.planned
    assert t4.delay_minutes is None
    assert t4.realtime is False


@pytest.mark.unit
def test_sorting_uses_real_time_estimate():
    """A late service is ordered by when it will actually leave."""
    data = {
        "stopEvents": [
            _event("late", planned="2026-11-07T03:00:00Z", estimated="2026-11-07T03:15:00Z"),
            _event("on_time", planned="2026-11-07T03:10:00Z"),
        ]
    }
    assert [d.line for d in parse_departures(data)] == ["on_time", "late"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "estimated, expected", [("2026-11-07T03:12:00Z", 2), ("2026-11-07T03:09:00Z", -1), ("2026-11-07T03:10:40Z", 1)]
)
def test_delay_minutes(estimated, expected):
    departure = Departure("T1", "", "", None, _utc(3, 10), datetime.fromisoformat(estimated), True, 1)
    assert departure.delay_minutes == expected


@pytest.mark.unit
def test_is_cancelled_flag_is_also_honoured():
    data = {"stopEvents": [{**_event("T1", planned="2026-11-07T03:00:00Z"), "isCancelled": True}]}
    assert parse_departures(data) == []


@pytest.mark.unit
def test_events_without_planned_time_are_skipped():
    data = {"stopEvents": [_event("T1", planned=None), _event("T2", planned="not a time")]}
    assert parse_departures(data) == []


@pytest.mark.unit
@pytest.mark.parametrize("data", [{}, {"stopEvents": None}, {"stopEvents": []}])
def test_empty_departure_responses(data):
    assert parse_departures(data) == []


@pytest.mark.unit
def test_platform_without_prefix_is_kept():
    data = {"stopEvents": [_event("T1", planned="2026-11-07T03:00:00Z", location=_platform("Central"))]}
    assert parse_departures(data)[0].platform == "Central"


@pytest.mark.unit
def test_missing_platform_is_none():
    data = {"stopEvents": [_event("T1", planned="2026-11-07T03:00:00Z", location={})]}
    assert parse_departures(data)[0].platform is None


@pytest.mark.unit
def test_alerts_without_a_title_are_ignored():
    infos = [{"id": "a", "priority": "low"}, {"id": "b", "urlText": "Read more"}]
    data = {"stopEvents": [_event("T1", planned="2026-11-07T03:00:00Z", infos=infos)]}
    assert [alert.title for alert in parse_departures(data)[0].alerts] == ["Read more"]


def _platform(name):
    return {"disassembledName": "Central Station, Sydney", "properties": {"platformName": name}}


def _event(line, planned, estimated=None, location=None, infos=None, product_class=1):
    return {
        "departureTimePlanned": planned,
        "departureTimeEstimated": estimated,
        "location": location if location is not None else _platform("Platform 1"),
        "transportation": {"disassembledName": line, "product": {"class": product_class}, "destination": {"name": "X"}},
        "infos": infos,
    }


@pytest.mark.unit
def test_alert_titles_are_trimmed():
    """Real alerts can end in a non-breaking space."""
    infos = [{"id": "a", "subtitle": "Some services may not be wheelchair accessible.\xa0"}, {"id": "b", "subtitle": "  "}]
    data = {"stopEvents": [_event("T1", planned="2026-11-07T03:00:00Z", infos=infos)]}
    assert [alert.title for alert in parse_departures(data)[0].alerts] == ["Some services may not be wheelchair accessible."]


# --- parse_stops ---


@pytest.mark.unit
def test_stops_only_rail_stations_best_match_first(stop_finder_json):
    stops = parse_stops(stop_finder_json)
    assert [stop.id for stop in stops] == ["200060", "209110", "2153401"]
    assert stops[0].name == "Central Station, Sydney"


@pytest.mark.unit
def test_stops_empty_response():
    assert parse_stops({}) == []


# --- TransportNswClient ---


@pytest.mark.unit
async def test_get_departures_request(departures_json):
    session = FakeSession(FakeResponse(200, departures_json))
    client = TransportNswClient(session, "secret")

    departures = await client.async_get_departures("200060", now=_utc(3, 0))

    assert [d.line for d in departures] == ["T1", "M1", "T8", "T4"]
    url, kwargs = session.calls[0]
    assert url == DEPARTURE_MON_ENDPOINT
    assert kwargs["headers"]["Authorization"] == "apikey secret"
    params = kwargs["params"]
    assert params["name_dm"] == "200060"
    assert params["outputFormat"] == "rapidJSON"
    # 03:00 UTC is 14:00 in Sydney during daylight saving
    assert (params["itdDate"], params["itdTime"]) == ("20261107", "1400")
    assert "exclMOT_5" in params
    assert "exclMOT_1" not in params
    assert "exclMOT_2" not in params


@pytest.mark.unit
async def test_get_departures_without_metro(departures_json):
    session = FakeSession(FakeResponse(200, departures_json))

    departures = await TransportNswClient(session, "secret").async_get_departures("200060", include_metro=False)

    assert "M1" not in [d.line for d in departures]
    assert session.calls[0][1]["params"]["exclMOT_2"] == "1"


@pytest.mark.unit
async def test_find_stops_request(stop_finder_json):
    session = FakeSession(FakeResponse(200, stop_finder_json))

    stops = await TransportNswClient(session, "secret").async_find_stops("Central")

    assert len(stops) == 3
    url, kwargs = session.calls[0]
    assert url == STOP_FINDER_ENDPOINT
    assert kwargs["params"]["name_sf"] == "Central"


@pytest.mark.unit
@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_api_key(status):
    client = TransportNswClient(FakeSession(FakeResponse(status, {})), "bad")
    with pytest.raises(InvalidApiKey):
        await client.async_get_departures("200060")


@pytest.mark.unit
async def test_server_error():
    client = TransportNswClient(FakeSession(FakeResponse(500, {})), "secret")
    with pytest.raises(TransportNswError):
        await client.async_get_departures("200060")


@pytest.mark.unit
@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError("down"), TimeoutError()])
async def test_network_errors(error):
    client = TransportNswClient(FakeSession(error=error), "secret")
    with pytest.raises(TransportNswError):
        await client.async_find_stops("Central")


@pytest.mark.unit
async def test_unexpected_body():
    client = TransportNswClient(FakeSession(FakeResponse(200, ["not", "a", "dict"])), "secret")
    with pytest.raises(TransportNswError):
        await client.async_find_stops("Central")
