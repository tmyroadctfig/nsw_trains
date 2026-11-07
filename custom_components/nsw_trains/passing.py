"""Predicts when trains pass a point on the line between two stations, and finds the nearest train by GPS."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .api import Departure
from .gtfs import VehiclePosition

EARTH_RADIUS_M = 6_371_000


@dataclass(frozen=True)
class PredictedPass:
    time: datetime
    # Name of the station the train is heading to next, which gives the direction
    towards: str
    line: str
    destination: str
    delay_minutes: int | None
    realtime: bool
    trip_id: str


@dataclass(frozen=True)
class NearbyTrain:
    vehicle: VehiclePosition
    distance_m: float


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def fraction_along(a: tuple[float, float], b: tuple[float, float], point: tuple[float, float]) -> float:
    """How far along the straight line from station a to station b the point sits, from 0 (at a) to 1 (at b).

    Uses a flat projection, which is accurate over the few kilometres between neighbouring stations.
    """
    scale = math.cos(math.radians((a[0] + b[0]) / 2))
    ax, ay = a[1] * scale, a[0]
    bx, by = b[1] * scale, b[0]
    px, py = point[1] * scale, point[0]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return 0.0
    return min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))


def predict_passes(
    departures_a: list[Departure],
    departures_b: list[Departure],
    fraction: float,
    name_a: str,
    name_b: str,
    since: datetime,
) -> list[PredictedPass]:
    """Trains that stop at both stations, with the time each passes the point `fraction` of the way from a to b.

    The same trip has the same id at both stations, and whichever station it leaves first gives its direction. The
    pass time is interpolated between the two (real-time where available) departure times. Passes before `since`
    are dropped.
    """
    from_b = {departure.trip_id: departure for departure in departures_b if departure.trip_id}
    passes = []
    for at_a in departures_a:
        at_b = from_b.get(at_a.trip_id) if at_a.trip_id else None
        if at_b is None:
            continue
        time_a, time_b = at_a.departure_time, at_b.departure_time
        if time_a <= time_b:
            first, towards, progress = at_a, name_b, fraction
            pass_time = time_a + (time_b - time_a) * fraction
        else:
            first, towards, progress = at_b, name_a, 1 - fraction
            pass_time = time_b + (time_a - time_b) * progress
        pass_time = pass_time.replace(microsecond=0)
        if pass_time < since:
            continue
        passes.append(
            PredictedPass(
                time=pass_time,
                towards=towards,
                line=first.line,
                destination=first.destination,
                delay_minutes=first.delay_minutes,
                realtime=first.realtime,
                trip_id=at_a.trip_id or "",
            )
        )
    passes.sort(key=lambda predicted: predicted.time)
    return passes


def next_pass(passes: list[PredictedPass], towards: str, now: datetime) -> PredictedPass | None:
    """The next train heading towards a station that hasn't passed yet."""
    return next((p for p in passes if p.towards == towards and p.time >= now), None)


def is_passing(passes: list[PredictedPass], now: datetime, window: timedelta) -> PredictedPass | None:
    """A predicted pass within `window` of now, if any."""
    return next((p for p in passes if abs(p.time - now) <= window), None)


def nearest_train(
    vehicles: list[VehiclePosition], latitude: float, longitude: float, now: datetime, max_age: timedelta
) -> NearbyTrain | None:
    """The closest train with a GPS position no older than max_age."""
    candidates = [
        NearbyTrain(vehicle, distance_m(latitude, longitude, vehicle.latitude, vehicle.longitude))
        for vehicle in vehicles
        if now - vehicle.timestamp <= max_age
    ]
    return min(candidates, key=lambda train: train.distance_m, default=None)
