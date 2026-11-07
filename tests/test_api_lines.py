"""Unit tests for line alerts, nearby stations and the realtime feed client methods."""

from datetime import datetime, timezone

import pytest

from custom_components.nsw_trains.api import (
    ADD_INFO_ENDPOINT,
    COORD_ENDPOINT,
    TRIP_UPDATES_ENDPOINT,
    VEHICLE_POSITIONS_ENDPOINT,
    TransportNswClient,
    TransportNswError,
    alert_line_code,
    parse_line_alerts,
    parse_nearby_stations,
)

from .conftest import load_fixture
from .helpers import FakeResponse, FakeSession, trip_update_feed, vehicle_feed

NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def add_info_json():
    return load_fixture("add_info.json")


@pytest.fixture
def coord_json():
    return load_fixture("coord.json")


@pytest.mark.unit
@pytest.mark.parametrize(
    "line_id, code",
    [
        ("nsw:020T4: :H:", "T4"),
        ("nsw:02SCO: :R:", "SCO"),
        ("nsw:76SCO: :H:", "SCO"),
        ("nsw:020T1:W:H:", "T1"),
        ("nsw:SCO: :H:", None),
        ("garbage", None),
        ("", None),
    ],
)
def test_alert_line_code(line_id, code):
    assert alert_line_code(line_id) == code


@pytest.mark.unit
def test_parse_line_alerts(add_info_json):
    alerts = {alert.id: alert for alert in parse_line_alerts(add_info_json)}

    # Alerts that don't affect a line are skipped
    assert set(alerts) == {"sco-trackwork-now", "sco-trackwork-later", "sco-standing", "t1-incident"}
    now_alert = alerts["sco-trackwork-now"]
    assert now_alert.lines == frozenset({"SCO", "T4"})
    assert now_alert.announcement_type == "trackwork"
    assert now_alert.priority == "normal"
    assert now_alert.info_type == "lineInfo"
    assert now_alert.url == "https://transportnsw.info/alerts/details#/sco-trackwork-now"
    assert alerts["t1-incident"].lines == frozenset({"T1"})


@pytest.mark.unit
def test_alert_windows(add_info_json):
    alerts = {alert.id: alert for alert in parse_line_alerts(add_info_json)}

    assert alerts["sco-trackwork-now"].is_active(NOW)
    assert alerts["sco-trackwork-now"].next_start(NOW) is None
    later = alerts["sco-trackwork-later"]
    assert not later.is_active(NOW)
    assert later.next_start(NOW) == datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    assert later.next_start(datetime(2026, 11, 21, tzinfo=timezone.utc)) == datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc)
    assert later.current_window(datetime(2026, 11, 21, tzinfo=timezone.utc))[0] == datetime(
        2026, 11, 20, 15, 0, tzinfo=timezone.utc
    )


@pytest.mark.unit
def test_parse_line_alerts_empty():
    assert parse_line_alerts({}) == []
    assert parse_line_alerts({"infos": {"current": None}}) == []


@pytest.mark.unit
def test_parse_nearby_stations(coord_json):
    stations = parse_nearby_stations(coord_json)

    # The bus stop is left out; nearest first; names come from STOP_NAME_WITH_PLACE, not "undefined, undefined"
    assert [(s.id, s.name) for s in stations] == [
        ("251540", "Austinmer Station, Austinmer"),
        ("251550", "Thirroul Station, Thirroul"),
        ("251530", "Coledale Station, Coledale"),
    ]
    assert (stations[0].latitude, stations[0].longitude) == (-34.306457, 150.929233)


@pytest.mark.unit
async def test_find_stations_near_request(coord_json):
    session = FakeSession(FakeResponse(200, coord_json))

    stations = await TransportNswClient(session, "secret").async_find_stations_near(-34.305, 150.929, 4000)

    assert len(stations) == 3
    url, kwargs = session.calls[0]
    assert url == COORD_ENDPOINT
    assert kwargs["params"]["coord"] == "150.929000:-34.305000:EPSG:4326"
    assert kwargs["params"]["radius_1"] == "4000"


@pytest.mark.unit
async def test_get_line_alerts_request(add_info_json):
    session = FakeSession(FakeResponse(200, add_info_json))

    alerts = await TransportNswClient(session, "secret").async_get_line_alerts()

    assert len(alerts) == 4
    url, kwargs = session.calls[0]
    assert url == ADD_INFO_ENDPOINT
    assert kwargs["params"]["filterMOTType"] == "1"
    assert kwargs["params"]["filterPublicationStatus"] == "current"


@pytest.mark.unit
async def test_get_vehicle_positions_request():
    content = vehicle_feed([{"trip_id": "a", "route_id": "SCO_1a", "lat": -34.3, "lon": 150.9, "timestamp": 1}])
    session = FakeSession(FakeResponse(200, content))

    positions = await TransportNswClient(session, "secret").async_get_vehicle_positions()

    assert [p.trip_id for p in positions] == ["a"]
    url, kwargs = session.calls[0]
    assert url == VEHICLE_POSITIONS_ENDPOINT
    assert kwargs["headers"]["Authorization"] == "apikey secret"


@pytest.mark.unit
async def test_get_trip_delays_request():
    session = FakeSession(FakeResponse(200, trip_update_feed([{"trip_id": "a", "route_id": "SCO_1a", "delay": 30}])))

    delays = await TransportNswClient(session, "secret").async_get_trip_delays()

    assert delays["a"].delay_seconds == 30
    assert session.calls[0][0] == TRIP_UPDATES_ENDPOINT


@pytest.mark.unit
@pytest.mark.parametrize("method", ["async_get_vehicle_positions", "async_get_trip_delays"])
async def test_corrupt_feed_is_an_api_error(method):
    client = TransportNswClient(FakeSession(FakeResponse(200, b"\xff\xff\xff junk")), "secret")
    with pytest.raises(TransportNswError):
        await getattr(client, method)()
