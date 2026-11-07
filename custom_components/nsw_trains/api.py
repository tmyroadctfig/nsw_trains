"""Client for the Transport for NSW Open Data APIs.

Docs: https://opendata.transport.nsw.gov.au/. Every request needs an API key, sent as ``Authorization: apikey <key>``.
Uses the Trip Planner API (JSON) for stations, departures and alerts, and the GTFS-realtime feeds (protobuf) for
live train positions and delays.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import HEADERS, NON_RAIL_PRODUCT_CLASSES, PRODUCT_CLASS_METRO, PRODUCT_CLASS_TRAIN
from .gtfs import FeedError, TripDelay, VehiclePosition, parse_trip_updates, parse_vehicle_positions

_LOGGER = logging.getLogger(__name__)

API_HOST = "https://api.transport.nsw.gov.au"
BASE_URL = f"{API_HOST}/v1/tp"
STOP_FINDER_ENDPOINT = f"{BASE_URL}/stop_finder"
DEPARTURE_MON_ENDPOINT = f"{BASE_URL}/departure_mon"
COORD_ENDPOINT = f"{BASE_URL}/coord"
ADD_INFO_ENDPOINT = f"{BASE_URL}/add_info"
# The Sydney Trains feeds also cover intercity lines such as the South Coast and Blue Mountains lines
VEHICLE_POSITIONS_ENDPOINT = f"{API_HOST}/v2/gtfs/vehiclepos/sydneytrains"
TRIP_UPDATES_ENDPOINT = f"{API_HOST}/v2/gtfs/realtime/sydneytrains"

SYDNEY_TZ = ZoneInfo("Australia/Sydney")

# realtimeStatus entry marking a cancelled service
CANCELLED_STATUS = "TRIP_CANCELLED"

RAIL_PRODUCT_CLASSES = frozenset({PRODUCT_CLASS_TRAIN, PRODUCT_CLASS_METRO})

COMMON_PARAMS = {
    "outputFormat": "rapidJSON",
    "coordOutputFormat": "EPSG:4326",
    "version": "10.2.1.42",
}

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Alert line ids look like "nsw:020T4: :H:" or "nsw:02SCO: :R:": an operator number followed by the line code
_ALERT_LINE_ID = re.compile(r"^\d+([A-Z][A-Z0-9]*)$")


class TransportNswError(Exception):
    """The API could not be reached or returned an unusable response."""


class InvalidApiKey(TransportNswError):
    """The API rejected the API key."""


@dataclass(frozen=True)
class Stop:
    id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class Alert:
    id: str
    title: str
    priority: str | None
    url: str | None


@dataclass(frozen=True)
class Departure:
    line: str
    line_name: str
    destination: str
    platform: str | None
    planned: datetime
    estimated: datetime | None
    realtime: bool
    product_class: int
    alerts: tuple[Alert, ...] = ()
    # Same id as the trip in the GTFS-realtime feeds, and across stations
    trip_id: str | None = None

    @property
    def departure_time(self) -> datetime:
        """Best estimate of when the service leaves: real-time when available, otherwise the timetable."""
        return self.estimated or self.planned

    @property
    def delay_minutes(self) -> int | None:
        """Minutes late (negative when early), or None without a real-time estimate."""
        if self.estimated is None:
            return None
        return round((self.estimated - self.planned).total_seconds() / 60)


@dataclass(frozen=True)
class LineAlert:
    """A service alert from add_info, e.g. trackwork or a station update, for one or more lines."""

    id: str
    title: str
    priority: str | None
    announcement_type: str | None
    info_type: str | None
    url: str | None
    lines: frozenset[str]
    windows: tuple[tuple[datetime, datetime], ...]

    def is_active(self, now: datetime) -> bool:
        return any(start <= now <= end for start, end in self.windows)

    def next_start(self, now: datetime) -> datetime | None:
        """Start of the next validity window after now, or None if there is none."""
        upcoming = [start for start, _ in self.windows if start > now]
        return min(upcoming) if upcoming else None

    def current_window(self, now: datetime) -> tuple[datetime, datetime] | None:
        return next(((start, end) for start, end in self.windows if start <= now <= end), None)


def _parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_platform(location: dict[str, Any]) -> str | None:
    """Turn a stop event location's platform name such as "Platform 16" into "16"."""
    name = (location.get("properties") or {}).get("platformName")
    if not isinstance(name, str) or not name:
        return None
    prefix = "Platform "
    return name[len(prefix) :] if name.startswith(prefix) else name


def _parse_alerts(infos: Any) -> tuple[Alert, ...]:
    alerts = []
    for info in infos or []:
        title = (info.get("subtitle") or info.get("urlText") or "").strip()
        if not title:
            continue
        alerts.append(
            Alert(
                id=str(info.get("id") or title),
                title=title,
                priority=info.get("priority"),
                url=info.get("url"),
            )
        )
    return tuple(alerts)


def _is_cancelled(event: dict[str, Any]) -> bool:
    return bool(event.get("isCancelled")) or CANCELLED_STATUS in (event.get("realtimeStatus") or [])


def _parse_coord(location: dict[str, Any]) -> tuple[float | None, float | None]:
    coord = location.get("coord")
    if isinstance(coord, list) and len(coord) == 2:
        return float(coord[0]), float(coord[1])
    return None, None


def parse_stops(data: dict[str, Any]) -> list[Stop]:
    """Extract train and metro stations from a stop_finder response, best match first."""
    stops = []
    for location in data.get("locations") or []:
        if location.get("type") != "stop":
            continue
        # "modes" holds the product classes served by the stop
        if not RAIL_PRODUCT_CLASSES.intersection(location.get("modes") or []):
            continue
        latitude, longitude = _parse_coord(location)
        stop = Stop(id=str(location["id"]), name=location.get("name") or "", latitude=latitude, longitude=longitude)
        stops.append((location.get("matchQuality") or 0, stop))
    stops.sort(key=lambda item: item[0], reverse=True)
    return [stop for _, stop in stops]


def parse_nearby_stations(data: dict[str, Any]) -> list[Stop]:
    """Extract train stations from a coord (nearby stops) response, nearest first.

    Unlike stop_finder, coord lists product classes under "productClasses", and its "name" is "undefined, undefined";
    the real name is in properties.STOP_NAME_WITH_PLACE.
    """
    stations = []
    for location in data.get("locations") or []:
        if PRODUCT_CLASS_TRAIN not in (location.get("productClasses") or []):
            continue
        properties = location.get("properties") or {}
        latitude, longitude = _parse_coord(location)
        if latitude is None or longitude is None:
            continue
        name = properties.get("STOP_NAME_WITH_PLACE") or location.get("name") or str(location["id"])
        stop = Stop(id=str(location["id"]), name=name, latitude=latitude, longitude=longitude)
        stations.append((properties.get("distance") or 0, stop))
    stations.sort(key=lambda item: item[0])
    return [stop for _, stop in stations]


def parse_departures(data: dict[str, Any], product_classes: frozenset[int] = RAIL_PRODUCT_CLASSES) -> list[Departure]:
    """Extract upcoming, non-cancelled departures of the given product classes, soonest first."""
    departures = []
    for event in data.get("stopEvents") or []:
        if _is_cancelled(event):
            continue

        transportation = event.get("transportation") or {}
        product_class = (transportation.get("product") or {}).get("class")
        if product_class not in product_classes:
            continue

        planned = _parse_time(event.get("departureTimePlanned"))
        if planned is None:
            continue

        departures.append(
            Departure(
                line=transportation.get("disassembledName") or "",
                line_name=transportation.get("number") or transportation.get("name") or "",
                destination=(transportation.get("destination") or {}).get("name") or "",
                platform=_parse_platform(event.get("location") or {}),
                planned=planned,
                estimated=_parse_time(event.get("departureTimeEstimated")),
                realtime=bool(event.get("isRealtimeControlled")),
                product_class=product_class,
                alerts=_parse_alerts(event.get("infos")),
                trip_id=(event.get("properties") or {}).get("RealtimeTripId"),
            )
        )

    departures.sort(key=lambda departure: departure.departure_time)
    return departures


def alert_line_code(line_id: str) -> str | None:
    """Line code from an add_info line id: "nsw:020T4: :H:" -> "T4", "nsw:02SCO: :R:" -> "SCO"."""
    parts = line_id.split(":")
    if len(parts) < 2:
        return None
    match = _ALERT_LINE_ID.match(parts[1])
    return match.group(1) if match else None


def parse_line_alerts(data: dict[str, Any]) -> list[LineAlert]:
    """Extract alerts that affect at least one line from an add_info response."""
    alerts = []
    for info in (data.get("infos") or {}).get("current") or []:
        lines = frozenset(
            code
            for line in (info.get("affected") or {}).get("lines") or []
            if (code := alert_line_code(line.get("id") or "")) is not None
        )
        title = (info.get("subtitle") or info.get("urlText") or "").strip()
        if not lines or not title:
            continue

        windows = []
        for window in (info.get("timestamps") or {}).get("validity") or []:
            start, end = _parse_time(window.get("from")), _parse_time(window.get("to"))
            if start is not None and end is not None:
                windows.append((start, end))

        alerts.append(
            LineAlert(
                id=str(info.get("id") or title),
                title=title,
                priority=info.get("priority"),
                announcement_type=(info.get("properties") or {}).get("announcementType"),
                info_type=info.get("type"),
                url=info.get("url"),
                lines=lines,
                windows=tuple(sorted(windows)),
            )
        )
    return alerts


class TransportNswClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key

    async def _request(self, url: str, params: dict[str, str] | None, as_json: bool) -> Any:
        headers = {**HEADERS, "Authorization": f"apikey {self._api_key}"}
        try:
            async with self._session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT) as response:
                if response.status in (401, 403):
                    raise InvalidApiKey(f"API key rejected ({response.status})")
                if response.status != 200:
                    raise TransportNswError(f"Unexpected response status {response.status}")
                if as_json:
                    return await response.json(content_type=None)
                return await response.read()
        except aiohttp.ClientError as err:
            raise TransportNswError(f"Request failed: {err}") from err
        except TimeoutError as err:
            raise TransportNswError("Request timed out") from err

    async def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        data = await self._request(url, {**COMMON_PARAMS, **params}, as_json=True)
        if not isinstance(data, dict):
            raise TransportNswError("Unexpected response body")
        return data

    async def async_find_stops(self, query: str) -> list[Stop]:
        """Search for train and metro stations by name."""
        data = await self._get(STOP_FINDER_ENDPOINT, {"type_sf": "any", "name_sf": query, "TfNSWSF": "true"})
        return parse_stops(data)

    async def async_find_stations_near(self, latitude: float, longitude: float, radius_m: int = 5000) -> list[Stop]:
        """Train stations within radius_m of a point, nearest first."""
        params = {
            "coord": f"{longitude:.6f}:{latitude:.6f}:EPSG:4326",
            "type_1": "STOP",
            "radius_1": str(radius_m),
            "inclFilter": "1",
            "coordListOutputFormat": "string",
        }
        return parse_nearby_stations(await self._get(COORD_ENDPOINT, params))

    async def async_get_departures(
        self, stop_id: str, include_metro: bool = True, now: datetime | None = None
    ) -> list[Departure]:
        """Fetch upcoming train (and optionally metro) departures from a station."""
        local_now = (now or datetime.now(timezone.utc)).astimezone(SYDNEY_TZ)
        excluded = list(NON_RAIL_PRODUCT_CLASSES) + ([] if include_metro else [PRODUCT_CLASS_METRO])
        params = {
            "mode": "direct",
            "type_dm": "stop",
            "name_dm": stop_id,
            "depArrMacro": "dep",
            "itdDate": local_now.strftime("%Y%m%d"),
            "itdTime": local_now.strftime("%H%M"),
            "TfNSWDM": "true",
            "excludedMeans": "checkbox",
            **{f"exclMOT_{product_class}": "1" for product_class in excluded},
        }
        data = await self._get(DEPARTURE_MON_ENDPOINT, params)

        product_classes = RAIL_PRODUCT_CLASSES if include_metro else frozenset({PRODUCT_CLASS_TRAIN})
        return parse_departures(data, product_classes)

    async def async_get_line_alerts(self) -> list[LineAlert]:
        """Current train alerts for all lines. add_info can't filter by line, so callers filter the result."""
        params = {"filterPublicationStatus": "current", "filterMOTType": str(PRODUCT_CLASS_TRAIN)}
        return parse_line_alerts(await self._get(ADD_INFO_ENDPOINT, params))

    async def async_get_vehicle_positions(self) -> list[VehiclePosition]:
        """Live GPS positions of Sydney Trains and intercity trains."""
        content = await self._request(VEHICLE_POSITIONS_ENDPOINT, None, as_json=False)
        try:
            return parse_vehicle_positions(content)
        except FeedError as err:
            raise TransportNswError(str(err)) from err

    async def async_get_trip_delays(self) -> dict[str, TripDelay]:
        """Live delay and cancellation of each Sydney Trains and intercity trip, by trip id."""
        content = await self._request(TRIP_UPDATES_ENDPOINT, None, as_json=False)
        try:
            return parse_trip_updates(content)
        except FeedError as err:
            raise TransportNswError(str(err)) from err
