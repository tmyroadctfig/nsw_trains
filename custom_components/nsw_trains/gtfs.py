"""Parsing of the GTFS-realtime (protobuf) vehicle position and trip update feeds."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

# GTFS route ids such as "SCO_1a" or "CTY_NC1a" start with an internal code. This maps it to the public line code
# used by the Trip Planner and alerts (route_short_name in the static GTFS routes.txt). RTTA_* (out of service and
# empty runs) are deliberately absent.
ROUTE_PREFIX_LINES = {
    "APS": "T8",
    "BMT": "BMT",
    "CCN": "CCN",
    "CMB": "T5",
    "CTY_NC": "NRC",
    "CTY_NW": "NRW",
    "CTY_S": "STH",
    "CTY_W": "WST",
    "ESI": "T4",
    "HUN": "HUN",
    "IWL": "T2",
    "NSN": "T1",
    "NTH": "T9",
    "OLY": "T7",
    "SCO": "SCO",
    "SHL": "SHL",
    "T3": "T3",
    "T6": "T6",
    "WST": "T1",
}

_ROUTE_ID = re.compile(r"^(.*?)_?\d+[a-z]?$")


class FeedError(Exception):
    """A GTFS-realtime feed could not be decoded."""


@dataclass(frozen=True)
class VehiclePosition:
    trip_id: str
    line: str
    # e.g. "13:59 Kiama Station to Bondi Junction Station"
    label: str
    latitude: float
    longitude: float
    timestamp: datetime


@dataclass(frozen=True)
class TripDelay:
    trip_id: str
    line: str
    delay_seconds: int | None
    cancelled: bool


def line_for_route(route_id: str) -> str | None:
    """Public line code for a GTFS route id: "SCO_1a" -> "SCO", "ESI_2d" -> "T4", "RTTA_DEF" -> None."""
    match = _ROUTE_ID.match(route_id)
    return ROUTE_PREFIX_LINES.get(match.group(1)) if match else None


def _decode(content: bytes) -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(content)
    except DecodeError as err:
        raise FeedError(f"Could not decode GTFS-realtime feed: {err}") from err
    return feed


def parse_vehicle_positions(content: bytes) -> list[VehiclePosition]:
    """Positions of trains on known lines. Out of service and empty runs are left out."""
    positions = []
    for entity in _decode(content).entity:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        line = line_for_route(vehicle.trip.route_id)
        if line is None or not vehicle.HasField("position"):
            continue
        positions.append(
            VehiclePosition(
                trip_id=vehicle.trip.trip_id,
                line=line,
                label=vehicle.vehicle.label.strip(),
                latitude=vehicle.position.latitude,
                longitude=vehicle.position.longitude,
                timestamp=datetime.fromtimestamp(vehicle.timestamp, timezone.utc),
            )
        )
    return positions


def _trip_delay_seconds(trip_update: gtfs_realtime_pb2.TripUpdate) -> int | None:
    """Delay at the trip's next stop.

    The feed has no absolute stop times, only delays, and lists the stops still to come, so the first stop with a
    delay is the next one.
    """
    for update in trip_update.stop_time_update:
        for event in (update.departure, update.arrival):
            if event.HasField("delay"):
                return int(event.delay)
    if trip_update.HasField("delay"):
        return int(trip_update.delay)
    return None


def parse_trip_updates(content: bytes) -> dict[str, TripDelay]:
    """Delay and cancellation for each trip on a known line, by trip id."""
    cancelled = gtfs_realtime_pb2.TripDescriptor.CANCELED
    delays = {}
    for entity in _decode(content).entity:
        if not entity.HasField("trip_update"):
            continue
        trip_update = entity.trip_update
        line = line_for_route(trip_update.trip.route_id)
        if line is None:
            continue
        trip_id = trip_update.trip.trip_id
        delays[trip_id] = TripDelay(
            trip_id=trip_id,
            line=line,
            delay_seconds=_trip_delay_seconds(trip_update),
            cancelled=trip_update.trip.schedule_relationship == cancelled,
        )
    return delays
