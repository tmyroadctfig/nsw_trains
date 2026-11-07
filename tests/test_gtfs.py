"""Unit tests for parsing the GTFS-realtime vehicle position and trip update feeds."""

from datetime import datetime, timezone

import pytest

from custom_components.nsw_trains.gtfs import FeedError, line_for_route, parse_trip_updates, parse_vehicle_positions

from .helpers import trip_update_feed, vehicle_feed

EPOCH = 1794021961


@pytest.mark.unit
@pytest.mark.parametrize(
    "route_id, line",
    [
        ("SCO_1a", "SCO"),
        ("SCO_2b", "SCO"),
        ("ESI_2d", "T4"),
        ("IWL_1j", "T2"),
        ("NSN_1a", "T1"),
        ("WST_2c", "T1"),
        ("T3_1a", "T3"),
        ("BMT_1", "BMT"),
        ("CTY_NC1a", "NRC"),
        ("CTY_S2i", "STH"),
        ("RTTA_DEF", None),
        ("RTTA_REV", None),
        ("XYZ_1a", None),
        ("", None),
    ],
)
def test_line_for_route(route_id, line):
    assert line_for_route(route_id) == line


@pytest.mark.unit
def test_vehicle_positions():
    content = vehicle_feed(
        [
            {
                "trip_id": "452D",
                "route_id": "SCO_2a",
                "label": "13:59 Kiama Station to Bondi Junction Station ",
                "lat": -34.3,
                "lon": 150.93,
                "timestamp": EPOCH,
            },
            {"trip_id": "out-of-service", "route_id": "RTTA_DEF", "lat": -33.8, "lon": 151.2, "timestamp": EPOCH},
            {"trip_id": "no-position", "route_id": "SCO_1a", "timestamp": EPOCH},
        ]
    )

    positions = parse_vehicle_positions(content)

    assert len(positions) == 1
    position = positions[0]
    assert position.trip_id == "452D"
    assert position.line == "SCO"
    assert position.label == "13:59 Kiama Station to Bondi Junction Station"
    assert position.latitude == pytest.approx(-34.3)
    assert position.longitude == pytest.approx(150.93)
    assert position.timestamp == datetime.fromtimestamp(EPOCH, timezone.utc)


@pytest.mark.unit
def test_trip_delay_is_from_next_stop():
    """Only remaining stops are listed, so the first one with a delay is the next stop."""
    content = trip_update_feed([{"trip_id": "a", "route_id": "SCO_1a", "stop_delays": [(None, 533), (600, 600)]}])
    assert parse_trip_updates(content)["a"].delay_seconds == 533


@pytest.mark.unit
def test_trip_delay_falls_back_to_arrival_then_trip_delay():
    content = trip_update_feed(
        [
            {"trip_id": "arrival", "route_id": "SCO_1a", "stop_delays": [(120, None)]},
            {"trip_id": "trip", "route_id": "SCO_1a", "delay": 60},
            {"trip_id": "none", "route_id": "SCO_1a"},
        ]
    )
    delays = parse_trip_updates(content)
    assert delays["arrival"].delay_seconds == 120
    assert delays["trip"].delay_seconds == 60
    assert delays["none"].delay_seconds is None


@pytest.mark.unit
def test_zero_delay_is_kept():
    content = trip_update_feed([{"trip_id": "a", "route_id": "SCO_1a", "stop_delays": [(0, 0)]}])
    assert parse_trip_updates(content)["a"].delay_seconds == 0


@pytest.mark.unit
def test_trip_updates_flag_cancellations_and_skip_unknown_routes():
    content = trip_update_feed(
        [
            {"trip_id": "cancelled", "route_id": "ESI_1a", "cancelled": True},
            {"trip_id": "empty-run", "route_id": "RTTA_REV", "stop_delays": [(0, 0)]},
        ]
    )
    delays = parse_trip_updates(content)
    assert set(delays) == {"cancelled"}
    assert delays["cancelled"].cancelled is True
    assert delays["cancelled"].line == "T4"


@pytest.mark.unit
@pytest.mark.parametrize("parse", [parse_vehicle_positions, parse_trip_updates])
def test_invalid_feed(parse):
    with pytest.raises(FeedError):
        parse(b"\xff\xff\xff not a protobuf")
