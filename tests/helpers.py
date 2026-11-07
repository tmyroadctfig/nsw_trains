"""Test doubles shared across unit tests."""

from typing import Any


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self._body = body

    async def json(self, content_type: str | None = None) -> Any:
        return self._body

    async def read(self) -> bytes:
        return bytes(self._body)

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class FakeSession:
    """Records requests and replies with a canned response, or raises the given error."""

    def __init__(self, response: FakeResponse | None = None, error: BaseException | None = None) -> None:
        self.response = response or FakeResponse()
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def vehicle_feed(vehicles: list[dict[str, Any]]) -> bytes:
    """GTFS-realtime vehicle positions: each dict has trip_id, route_id, label, lat, lon and timestamp (epoch)."""
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for index, vehicle in enumerate(vehicles):
        entity = feed.entity.add(id=str(index))
        entity.vehicle.trip.trip_id = vehicle["trip_id"]
        entity.vehicle.trip.route_id = vehicle["route_id"]
        entity.vehicle.vehicle.label = vehicle.get("label", "")
        if "lat" in vehicle:
            entity.vehicle.position.latitude = vehicle["lat"]
            entity.vehicle.position.longitude = vehicle["lon"]
        entity.vehicle.timestamp = vehicle["timestamp"]
    return bytes(feed.SerializeToString())


def trip_update_feed(trips: list[dict[str, Any]]) -> bytes:
    """GTFS-realtime trip updates: each dict has trip_id, route_id and optionally stop_delays (list of
    (arrival_delay, departure_delay), either may be None), delay (trip-level) and cancelled."""
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for index, trip in enumerate(trips):
        entity = feed.entity.add(id=str(index))
        update = entity.trip_update
        update.trip.trip_id = trip["trip_id"]
        update.trip.route_id = trip["route_id"]
        if trip.get("cancelled"):
            update.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.CANCELED
        if trip.get("delay") is not None:
            update.delay = trip["delay"]
        for arrival, departure in trip.get("stop_delays", []):
            stop = update.stop_time_update.add(stop_id="1")
            if arrival is not None:
                stop.arrival.delay = arrival
            if departure is not None:
                stop.departure.delay = departure
    return bytes(feed.SerializeToString())
