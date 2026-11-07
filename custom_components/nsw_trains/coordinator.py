"""Coordinators poll the API for one config entry and share the result with that entry's entities."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Departure, InvalidApiKey, Stop, TransportNswClient, TransportNswError
from .const import (
    ALERTS_MAX_AGE,
    CONF_INCLUDE_METRO,
    CONF_LATITUDE,
    CONF_LINE,
    CONF_LONGITUDE,
    CONF_PLATFORM,
    CONF_STATION_A,
    CONF_STATION_B,
    CONF_STOP_ID,
    DEFAULT_INCLUDE_METRO,
    DOMAIN,
    HOME_UPDATE_INTERVAL,
    REALTIME_MAX_AGE,
    UPDATE_INTERVAL,
    VEHICLE_MAX_AGE,
)
from .feeds import FeedCache, get_feed_cache
from .line_status import LineStatus, compute_line_status
from .passing import NearbyTrain, PredictedPass, fraction_along, nearest_train, predict_passes

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

# Departures for a home entry's two stations. Trains on the South Coast run hourly at times, so the default 40
# departures cover many hours; a minute-old list is plenty fresh for predicting passes.
DEPARTURES_MAX_AGE = timedelta(seconds=60)
# departure_mon only lists trains that haven't left yet. Asking from a little earlier keeps a train that has left one
# station but not reached the other in both lists, which is exactly when it passes the house.
DEPARTURES_LOOKBACK = timedelta(minutes=15)


async def _call_api(fetch: Callable[[], Awaitable[T]]) -> T:
    """Map API errors onto what the coordinator expects: reauth for a bad key, a failed update otherwise."""
    try:
        return await fetch()
    except InvalidApiKey as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except TransportNswError as err:
        raise UpdateFailed(str(err)) from err


def filter_platform(departures: list[Departure], platform: str | None) -> list[Departure]:
    """Keep only departures from the given platform; an empty platform keeps everything."""
    if not platform:
        return departures
    wanted = platform.strip().lower()
    return [departure for departure in departures if (departure.platform or "").strip().lower() == wanted]


def stop_from_data(data: dict[str, Any]) -> Stop:
    return Stop(id=data["id"], name=data["name"], latitude=data["latitude"], longitude=data["longitude"])


def stop_to_data(stop: Stop) -> dict[str, Any]:
    return {"id": stop.id, "name": stop.name, "latitude": stop.latitude, "longitude": stop.longitude}


class NswTrainsCoordinator(DataUpdateCoordinator[list[Departure]]):
    """Departures from one station."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TransportNswClient) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=UPDATE_INTERVAL, config_entry=entry)
        self.client = client
        self.stop_id: str = entry.data[CONF_STOP_ID]
        self.include_metro: bool = entry.options.get(CONF_INCLUDE_METRO, DEFAULT_INCLUDE_METRO)
        self.platform: str | None = entry.options.get(CONF_PLATFORM) or None

    async def _async_update_data(self) -> list[Departure]:
        departures = await _call_api(lambda: self.client.async_get_departures(self.stop_id, include_metro=self.include_metro))
        return filter_platform(departures, self.platform)


class LineCoordinator(DataUpdateCoordinator[LineStatus]):
    """Status of one line, from network-wide alerts, train positions and delays shared with other entries."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TransportNswClient, cache: FeedCache | None = None
    ) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=UPDATE_INTERVAL, config_entry=entry)
        self.client = client
        self.line: str = entry.data[CONF_LINE]
        self._cache = cache or get_feed_cache(hass)

    async def _async_update_data(self) -> LineStatus:
        cache, client = self._cache, self.client
        alerts = await _call_api(lambda: cache.async_get("alerts", ALERTS_MAX_AGE, client.async_get_line_alerts))
        vehicles = await _call_api(lambda: cache.async_get("vehicles", REALTIME_MAX_AGE, client.async_get_vehicle_positions))
        delays = await _call_api(lambda: cache.async_get("delays", REALTIME_MAX_AGE, client.async_get_trip_delays))
        return compute_line_status(self.line, alerts, vehicles, delays, datetime.now(timezone.utc))


@dataclass
class HomeData:
    passes: list[PredictedPass] = field(default_factory=list)
    nearest: NearbyTrain | None = None


class HomeCoordinator(DataUpdateCoordinator[HomeData]):
    """Trains passing a point on the line, predicted from the stations either side and tracked by GPS."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TransportNswClient, cache: FeedCache | None = None
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=HOME_UPDATE_INTERVAL, config_entry=entry
        )
        self.client = client
        self._cache = cache or get_feed_cache(hass)
        self.latitude: float = entry.data[CONF_LATITUDE]
        self.longitude: float = entry.data[CONF_LONGITUDE]
        self.station_a = stop_from_data(entry.data[CONF_STATION_A])
        self.station_b = stop_from_data(entry.data[CONF_STATION_B])
        self.fraction = fraction_along(
            (self.station_a.latitude or 0.0, self.station_a.longitude or 0.0),
            (self.station_b.latitude or 0.0, self.station_b.longitude or 0.0),
            (self.latitude, self.longitude),
        )

    async def _departures(self, stop: Stop) -> list[Departure]:
        return await _call_api(
            lambda: self._cache.async_get(
                f"departures:{stop.id}",
                DEPARTURES_MAX_AGE,
                lambda: self.client.async_get_departures(
                    stop.id, include_metro=False, now=datetime.now(timezone.utc) - DEPARTURES_LOOKBACK
                ),
            )
        )

    async def _async_update_data(self) -> HomeData:
        now = datetime.now(timezone.utc)
        departures_a = await self._departures(self.station_a)
        departures_b = await self._departures(self.station_b)
        cache, client = self._cache, self.client
        vehicles = await _call_api(lambda: cache.async_get("vehicles", REALTIME_MAX_AGE, client.async_get_vehicle_positions))

        # Keep recent passes too, so "passing now" still works just after the predicted time
        passes = predict_passes(
            departures_a,
            departures_b,
            self.fraction,
            self.station_a.name,
            self.station_b.name,
            since=now - timedelta(minutes=5),
        )
        return HomeData(passes=passes, nearest=nearest_train(vehicles, self.latitude, self.longitude, now, VEHICLE_MAX_AGE))
