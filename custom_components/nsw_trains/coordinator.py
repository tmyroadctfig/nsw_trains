"""Coordinators poll the API for one config entry and share the result with that entry's entities."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Departure, InvalidApiKey, RateLimited, Stop, TransportNswClient, TransportNswError
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


# TfNSW occasionally rejects a good key for a request or two (seen on the GTFS-realtime feeds), so a key that has
# worked must be rejected this many updates in a row before asking the user for a new one.
AUTH_FAILURES_BEFORE_REAUTH = 3
# How long to wait after hitting the rate limit or quota, unless the API asks for longer.
RATE_LIMIT_BACKOFF = timedelta(minutes=2)


class _ApiCoordinator(DataUpdateCoordinator[T]):
    """Maps API errors onto what the coordinator expects; subclasses implement _async_fetch."""

    _auth_failures = 0
    _has_succeeded = False

    async def _async_fetch(self) -> T:
        raise NotImplementedError

    async def _async_update_data(self) -> T:
        try:
            data = await self._async_fetch()
        except InvalidApiKey as err:
            self._auth_failures += 1
            # A key that has never worked here (setup, or a key changed in reauth) is reported straight away
            if not self._has_succeeded or self._auth_failures >= AUTH_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(f"{err}; retrying ({self._auth_failures} of {AUTH_FAILURES_BEFORE_REAUTH})") from err
        except RateLimited as err:
            retry_after = max(err.retry_after or 0, RATE_LIMIT_BACKOFF.total_seconds())
            raise UpdateFailed(f"{err}; retrying in {retry_after:.0f}s", retry_after=retry_after) from err
        except TransportNswError as err:
            raise UpdateFailed(str(err)) from err
        self._auth_failures = 0
        self._has_succeeded = True
        return data


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


class NswTrainsCoordinator(_ApiCoordinator[list[Departure]]):
    """Departures from one station."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TransportNswClient) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=UPDATE_INTERVAL, config_entry=entry)
        self.client = client
        self.stop_id: str = entry.data[CONF_STOP_ID]
        self.include_metro: bool = entry.options.get(CONF_INCLUDE_METRO, DEFAULT_INCLUDE_METRO)
        self.platform: str | None = entry.options.get(CONF_PLATFORM) or None

    async def _async_fetch(self) -> list[Departure]:
        departures = await self.client.async_get_departures(self.stop_id, include_metro=self.include_metro)
        return filter_platform(departures, self.platform)


class LineCoordinator(_ApiCoordinator[LineStatus]):
    """Status of one line, from network-wide alerts, train positions and delays shared with other entries."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TransportNswClient, cache: FeedCache | None = None
    ) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=UPDATE_INTERVAL, config_entry=entry)
        self.client = client
        self.line: str = entry.data[CONF_LINE]
        self._cache = cache or get_feed_cache(hass)

    async def _async_fetch(self) -> LineStatus:
        cache, client = self._cache, self.client
        alerts = await cache.async_get("alerts", ALERTS_MAX_AGE, client.async_get_line_alerts)
        vehicles = await cache.async_get("vehicles", REALTIME_MAX_AGE, client.async_get_vehicle_positions)
        delays = await cache.async_get("delays", REALTIME_MAX_AGE, client.async_get_trip_delays)
        return compute_line_status(self.line, alerts, vehicles, delays, datetime.now(timezone.utc))


@dataclass
class HomeData:
    passes: list[PredictedPass] = field(default_factory=list)
    nearest: NearbyTrain | None = None


class HomeCoordinator(_ApiCoordinator[HomeData]):
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
        return await self._cache.async_get(
            f"departures:{stop.id}",
            DEPARTURES_MAX_AGE,
            lambda: self.client.async_get_departures(
                stop.id, include_metro=False, now=datetime.now(timezone.utc) - DEPARTURES_LOOKBACK
            ),
        )

    async def _async_fetch(self) -> HomeData:
        now = datetime.now(timezone.utc)
        departures_a = await self._departures(self.station_a)
        departures_b = await self._departures(self.station_b)
        cache, client = self._cache, self.client
        vehicles = await cache.async_get("vehicles", REALTIME_MAX_AGE, client.async_get_vehicle_positions)

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
