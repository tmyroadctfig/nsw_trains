"""Unit tests for the departure and alert sensors."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.nsw_trains.api import LineAlert, parse_departures
from custom_components.nsw_trains.const import (
    CONF_DEPARTURE_COUNT,
    CONF_ENTRY_TYPE,
    CONF_LINE,
    CONF_STOP_ID,
    ENTRY_TYPE_HOME,
    ENTRY_TYPE_LINE,
)
from custom_components.nsw_trains.coordinator import HomeData
from custom_components.nsw_trains.gtfs import VehiclePosition
from custom_components.nsw_trains.line_status import LineStatus, RunningTrain
from custom_components.nsw_trains.passing import NearbyTrain, PredictedPass
from custom_components.nsw_trains.sensor import (
    AlertsSensor,
    DepartureSensor,
    LineAlertsSensor,
    LineStatusSensor,
    NearestTrainSensor,
    NextPassSensor,
    NextTrackworkSensor,
    OnTimeSensor,
    async_setup_entry,
    departure_attributes,
    unique_alerts,
)

NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)


def make_coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


@pytest.mark.unit
def test_departure_sensors_follow_order(departures_json):
    coordinator = make_coordinator(parse_departures(departures_json))

    first = DepartureSensor(coordinator, "200060", "Central", 0)
    third = DepartureSensor(coordinator, "200060", "Central", 2)

    assert first.native_value == datetime(2026, 11, 7, 3, 4, tzinfo=timezone.utc)
    assert first.extra_state_attributes["line"] == "T1"
    assert third.native_value == datetime(2026, 11, 7, 3, 12, tzinfo=timezone.utc)
    assert third.extra_state_attributes["destination"] == "Macarthur"


@pytest.mark.unit
def test_departure_sensor_names_and_ids():
    coordinator = make_coordinator([])

    first = DepartureSensor(coordinator, "200060", "Central", 0)
    second = DepartureSensor(coordinator, "200060", "Central", 1)

    assert (first._attr_name, first._attr_unique_id) == ("Next departure", "200060_departure_1")
    assert (second._attr_name, second._attr_unique_id) == ("Departure 2", "200060_departure_2")


@pytest.mark.unit
@pytest.mark.parametrize("data", [None, []])
def test_departure_sensor_without_enough_departures(data):
    sensor = DepartureSensor(make_coordinator(data), "200060", "Central", 0)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.unit
def test_departure_attributes(departures_json):
    t8 = next(d for d in parse_departures(departures_json) if d.line == "T8")

    assert departure_attributes(t8, now=NOW) == {
        "line": "T8",
        "line_name": "T8 Airport & South Line",
        "destination": "Macarthur",
        "platform": "18",
        "scheduled": "2026-11-07T03:10:00+00:00",
        "estimated": "2026-11-07T03:12:00+00:00",
        "delay_minutes": 2,
        "realtime": True,
        "due_in_minutes": 12,
    }


@pytest.mark.unit
def test_due_in_never_negative(departures_json):
    departure = parse_departures(departures_json)[0]
    later = datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)

    assert departure_attributes(departure, now=later)["due_in_minutes"] == 0


@pytest.mark.unit
def test_alerts_are_deduplicated(departures_json):
    alerts = unique_alerts(parse_departures(departures_json))
    assert [alert.id for alert in alerts] == ["alert-trackwork", "alert-lift"]


@pytest.mark.unit
def test_alerts_sensor(departures_json):
    sensor = AlertsSensor(make_coordinator(parse_departures(departures_json)), "200060", "Central")

    assert sensor._attr_unique_id == "200060_alerts"
    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["alerts"][0] == {
        "title": "Trackwork between Penrith and Emu Plains",
        "priority": "high",
        "url": "https://transportnsw.info/alerts/details#/trackwork",
    }


@pytest.mark.unit
def test_alerts_sensor_without_data():
    sensor = AlertsSensor(make_coordinator(None), "200060", "Central")

    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"alerts": []}


@pytest.mark.unit
@pytest.mark.parametrize(
    "options, expected_departures", [({}, 3), ({CONF_DEPARTURE_COUNT: 1}, 1), ({CONF_DEPARTURE_COUNT: 9}, 5)]
)
async def test_setup_creates_configured_number_of_sensors(options, expected_departures):
    entry = MagicMock()
    entry.title = "Central"
    entry.data = {CONF_STOP_ID: "200060"}
    entry.options = options
    entry.runtime_data = make_coordinator([])
    add_entities = MagicMock()

    await async_setup_entry(MagicMock(), entry, add_entities)

    entities = add_entities.call_args.args[0]
    assert sum(isinstance(e, DepartureSensor) for e in entities) == expected_departures
    assert sum(isinstance(e, AlertsSensor) for e in entities) == 1


# --- Line sensors ---

LINE_NOW = datetime.now(timezone.utc)


def line_status(**kwargs):
    defaults = dict(status="delays", reason="2 of 3 trains running 6+ minutes late, up to 8 minutes", on_time_minutes=6)
    return LineStatus(**{**defaults, **kwargs})


def line_alert(id_, start, end, kind="trackwork"):
    return LineAlert(
        id_, f"Alert {id_}", "normal", kind, "lineInfo", f"https://example/{id_}", frozenset({"SCO"}), ((start, end),)
    )


@pytest.mark.unit
def test_line_status_sensor():
    sensor = LineStatusSensor(make_coordinator(line_status()), "SCO", "South Coast Line")

    assert sensor._attr_unique_id == "line_SCO_status"
    assert sensor.native_value == "delays"
    assert sensor.extra_state_attributes == {"reason": "2 of 3 trains running 6+ minutes late, up to 8 minutes"}


@pytest.mark.unit
def test_line_sensors_without_data():
    coordinator = make_coordinator(None)
    for sensor_class in (LineStatusSensor, OnTimeSensor, LineAlertsSensor, NextTrackworkSensor):
        sensor = sensor_class(coordinator, "SCO", "South Coast Line")
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}


@pytest.mark.unit
def test_on_time_sensor():
    running = [RunningTrain("a", "13:59 Kiama to Bondi Junction", 8.0), RunningTrain("b", "14:10 Kiama", 1.0)]
    sensor = OnTimeSensor(make_coordinator(line_status(running=running, cancelled_trips=2)), "SCO", "South Coast Line")

    assert sensor.native_value == 50
    assert sensor.extra_state_attributes == {
        "trains_running": 2,
        "late_trains": [{"train": "13:59 Kiama to Bondi Junction", "delay_minutes": 8.0}],
        "on_time_threshold_minutes": 6,
        "average_delay_minutes": 4.5,
        "max_delay_minutes": 8.0,
        "cancelled_trips": 2,
    }


@pytest.mark.unit
def test_line_alerts_sensor():
    active = line_alert(
        "now", LINE_NOW - timedelta(hours=1), datetime(2500, 12, 31, tzinfo=timezone.utc), kind="serviceChange"
    )
    upcoming = [line_alert(f"later{i}", LINE_NOW + timedelta(days=i + 1), LINE_NOW + timedelta(days=i + 2)) for i in range(7)]
    sensor = LineAlertsSensor(make_coordinator(line_status(active_alerts=[active], upcoming_trackwork=upcoming)), "SCO", "SCO")

    assert sensor.native_value == 1
    attributes = sensor.extra_state_attributes
    assert attributes["alerts"] == [
        {
            "title": "Alert now",
            "type": "serviceChange",
            "priority": "normal",
            "start": (LINE_NOW - timedelta(hours=1)).isoformat(),
            # Open-ended alerts run to 2500; that's not shown
            "end": None,
            "url": "https://example/now",
        }
    ]
    assert len(attributes["upcoming_trackwork"]) == 5
    assert attributes["upcoming_trackwork"][0]["start"] == (LINE_NOW + timedelta(days=1)).isoformat()
    assert attributes["upcoming_trackwork"][0]["end"] == (LINE_NOW + timedelta(days=2)).isoformat()


@pytest.mark.unit
def test_next_trackwork_sensor():
    upcoming = line_alert("soon", LINE_NOW + timedelta(days=2), LINE_NOW + timedelta(days=3))
    sensor = NextTrackworkSensor(make_coordinator(line_status(upcoming_trackwork=[upcoming])), "SCO", "SCO")

    assert sensor.native_value == LINE_NOW + timedelta(days=2)
    assert sensor.extra_state_attributes["title"] == "Alert soon"


@pytest.mark.unit
def test_next_trackwork_sensor_when_none_planned():
    sensor = NextTrackworkSensor(make_coordinator(line_status()), "SCO", "SCO")
    assert sensor.native_value is None


# --- Home sensors ---


def home_coordinator(passes=(), nearest=None):
    coordinator = make_coordinator(HomeData(passes=list(passes), nearest=nearest))
    coordinator.station_a.name = "Austinmer Station, Austinmer"
    coordinator.station_b.name = "Thirroul Station, Thirroul"
    return coordinator


def predicted(trip_id, towards, minutes):
    return PredictedPass(LINE_NOW + timedelta(minutes=minutes), towards, "SCO", "Kiama", 2, True, trip_id)


@pytest.mark.unit
def test_next_pass_sensor():
    passes = [
        predicted("gone", "Thirroul Station, Thirroul", -3),
        predicted("north", "Austinmer Station, Austinmer", 4),
        predicted("south", "Thirroul Station, Thirroul", 9),
    ]
    sensor = NextPassSensor(home_coordinator(passes), "entry", "Home", "Thirroul Station, Thirroul")

    assert sensor._attr_name == "Next train towards Thirroul"
    assert sensor._attr_unique_id == "entry_next_towards_thirroul"
    assert sensor.native_value == LINE_NOW + timedelta(minutes=9)
    attributes = sensor.extra_state_attributes
    assert attributes["line"] == "SCO"
    assert attributes["destination"] == "Kiama"
    assert attributes["delay_minutes"] == 2
    assert attributes["due_in_minutes"] in (8, 9)


@pytest.mark.unit
def test_next_pass_sensor_without_upcoming_trains():
    sensor = NextPassSensor(home_coordinator(), "entry", "Home", "Austinmer Station, Austinmer")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.unit
def test_nearest_train_sensor():
    vehicle = VehiclePosition("t", "SCO", "13:59 Kiama to Bondi Junction", -34.31, 150.92, LINE_NOW - timedelta(seconds=45))
    sensor = NearestTrainSensor(home_coordinator(nearest=NearbyTrain(vehicle, 812.4)), "entry", "Home")

    assert sensor.native_value == 812
    attributes = sensor.extra_state_attributes
    assert attributes["train"] == "13:59 Kiama to Bondi Junction"
    assert (attributes["latitude"], attributes["longitude"]) == (-34.31, 150.92)
    assert 45 <= attributes["position_age_seconds"] <= 47


@pytest.mark.unit
def test_nearest_train_sensor_without_trains():
    sensor = NearestTrainSensor(home_coordinator(), "entry", "Home")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.unit
@pytest.mark.parametrize(
    "entry_type, expected",
    [
        (ENTRY_TYPE_LINE, [LineStatusSensor, OnTimeSensor, LineAlertsSensor, NextTrackworkSensor]),
        (ENTRY_TYPE_HOME, [NextPassSensor, NextPassSensor, NearestTrainSensor]),
    ],
)
async def test_setup_creates_sensors_for_entry_type(entry_type, expected):
    entry = MagicMock()
    entry.title = "Test"
    entry.entry_id = "entry"
    entry.data = {CONF_ENTRY_TYPE: entry_type, CONF_LINE: "SCO"}
    entry.runtime_data = home_coordinator()
    add_entities = MagicMock()

    await async_setup_entry(MagicMock(), entry, add_entities)

    assert [type(e) for e in add_entities.call_args.args[0]] == expected
