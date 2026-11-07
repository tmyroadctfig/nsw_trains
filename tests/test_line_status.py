"""Unit tests for working out a line's status."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.nsw_trains.api import LineAlert
from custom_components.nsw_trains.const import (
    LINE_STATUS_DELAYS,
    LINE_STATUS_DISRUPTION,
    LINE_STATUS_GOOD,
    LINE_STATUS_TRACKWORK,
)
from custom_components.nsw_trains.gtfs import TripDelay, VehiclePosition
from custom_components.nsw_trains.line_status import compute_line_status, on_time_minutes, running_trains

NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
FAR_FUTURE = datetime(2500, 12, 31, tzinfo=timezone.utc)


def alert(
    id_,
    lines=("SCO",),
    priority="normal",
    kind="serviceChange",
    start=NOW - timedelta(hours=1),
    end=FAR_FUTURE,
    info_type="lineInfo",
):
    return LineAlert(id_, f"Alert {id_}", priority, kind, info_type, None, frozenset(lines), ((start, end),))


def vehicle(trip_id, line="SCO", age=timedelta(seconds=30)):
    return VehiclePosition(trip_id, line, f"Train {trip_id}", -34.3, 150.9, NOW - age)


def delay(trip_id, minutes, line="SCO", cancelled=False):
    return TripDelay(trip_id, line, None if minutes is None else int(minutes * 60), cancelled)


def status_for(alerts=(), trains=(), line="SCO"):
    """trains: (trip_id, delay_minutes) pairs, each running on the line."""
    vehicles = [vehicle(trip_id, line) for trip_id, _ in trains]
    delays = {trip_id: delay(trip_id, minutes, line) for trip_id, minutes in trains}
    return compute_line_status(line, list(alerts), vehicles, delays, NOW)


@pytest.mark.unit
def test_good_service():
    status = status_for(trains=[("a", 0), ("b", 2)])

    assert status.status == LINE_STATUS_GOOD
    assert status.on_time_percent == 100
    assert status.average_delay_minutes == 1
    assert status.max_delay_minutes == 2


@pytest.mark.unit
def test_no_trains_running_has_no_on_time_figure():
    status = status_for()

    assert status.status == LINE_STATUS_GOOD
    assert status.on_time_percent is None
    assert status.max_delay_minutes is None
    assert status.average_delay_minutes is None


@pytest.mark.unit
def test_intercity_on_time_threshold():
    assert on_time_minutes("SCO") == 6
    assert on_time_minutes("T4") == 5
    # 5.5 minutes late is on time on the South Coast, but late on T4
    assert status_for(trains=[("a", 5.5)]).late == []
    assert len(status_for(trains=[("a", 5.5)], line="T4").late) == 1


@pytest.mark.unit
def test_delays_when_too_few_trains_on_time():
    status = status_for(trains=[("a", 0), ("b", 7), ("c", 8)])

    assert status.status == LINE_STATUS_DELAYS
    assert status.on_time_percent == 33.3
    assert status.reason == "2 of 3 trains running 6+ minutes late, up to 8 minutes"


@pytest.mark.unit
def test_delays_when_one_train_is_very_late():
    trains = [(str(i), 0) for i in range(9)] + [("late", 20)]
    status = status_for(trains=trains)

    assert status.on_time_percent == 90
    assert status.status == LINE_STATUS_DELAYS


@pytest.mark.unit
def test_trackwork_now():
    status = status_for(alerts=[alert("tw", kind="trackwork", end=NOW + timedelta(hours=2))], trains=[("a", 0)])

    assert status.status == LINE_STATUS_TRACKWORK
    assert status.reason == "Alert tw"


@pytest.mark.unit
def test_delays_outrank_trackwork():
    status = status_for(alerts=[alert("tw", kind="trackwork")], trains=[("a", 10)])
    assert status.status == LINE_STATUS_DELAYS


@pytest.mark.unit
@pytest.mark.parametrize("priority", ["high", "veryHigh"])
def test_high_priority_alert_is_a_disruption(priority):
    status = status_for(alerts=[alert("incident", priority=priority)], trains=[("a", 10)])

    assert status.status == LINE_STATUS_DISRUPTION
    assert status.reason == "Alert incident"


@pytest.mark.unit
def test_only_this_lines_active_alerts_count():
    alerts = [
        alert("other-line", lines=("T1",), priority="high"),
        alert("finished", end=NOW - timedelta(minutes=1), priority="high"),
        alert("standing", priority="veryLow"),
    ]
    status = status_for(alerts=alerts)

    assert status.status == LINE_STATUS_GOOD
    assert [a.id for a in status.active_alerts] == ["standing"]


@pytest.mark.unit
def test_upcoming_trackwork_sorted_by_start():
    alerts = [
        alert("later", kind="trackwork", start=NOW + timedelta(days=9), end=NOW + timedelta(days=10)),
        alert("sooner", kind="trackwork", start=NOW + timedelta(days=2), end=NOW + timedelta(days=3)),
        alert("not-trackwork", start=NOW + timedelta(days=1), end=NOW + timedelta(days=2)),
        alert("now", kind="trackwork"),
    ]
    status = status_for(alerts=alerts)

    assert [a.id for a in status.upcoming_trackwork] == ["sooner", "later"]


@pytest.mark.unit
def test_running_trains_needs_fresh_position_and_live_delay():
    vehicles = [
        vehicle("ok"),
        vehicle("stale", age=timedelta(minutes=10)),
        vehicle("other-line", line="T4"),
        vehicle("no-delay"),
        vehicle("unknown-delay"),
        vehicle("cancelled"),
    ]
    delays = {
        "ok": delay("ok", 3),
        "stale": delay("stale", 0),
        "other-line": delay("other-line", 0, line="T4"),
        "unknown-delay": delay("unknown-delay", None),
        "cancelled": delay("cancelled", 0, cancelled=True),
    }

    trains = running_trains("SCO", vehicles, delays, NOW)

    assert [(t.trip_id, t.delay_minutes, t.label) for t in trains] == [("ok", 3.0, "Train ok")]


@pytest.mark.unit
def test_running_trains_most_delayed_first():
    status = status_for(trains=[("a", 1), ("b", 9), ("c", 4)])
    assert [t.trip_id for t in status.running] == ["b", "c", "a"]


@pytest.mark.unit
def test_cancelled_trips_counted_for_line():
    delays = {
        "x": delay("x", None, cancelled=True),
        "y": delay("y", None, cancelled=True),
        "z": delay("z", None, line="T4", cancelled=True),
    }
    assert compute_line_status("SCO", [], [], delays, NOW).cancelled_trips == 2


@pytest.mark.unit
def test_trip_messages_are_not_line_alerts():
    """Per-trip "Running late" notices are often left open for years; live delays cover lateness instead."""
    status = status_for(alerts=[alert("running-late", info_type="tripMessage", priority="high"), alert("line")])

    assert [a.id for a in status.active_alerts] == ["line"]
    assert status.status == LINE_STATUS_GOOD
