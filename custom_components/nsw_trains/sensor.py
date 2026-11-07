"""Sensor platform for NSW Trains."""

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import Alert, Departure, LineAlert
from .const import (
    CONF_DEPARTURE_COUNT,
    CONF_ENTRY_TYPE,
    CONF_LINE,
    CONF_STOP_ID,
    DEFAULT_DEPARTURE_COUNT,
    ENTRY_TYPE_HOME,
    ENTRY_TYPE_LINE,
    ENTRY_TYPE_STATION,
    LINE_STATUSES,
    MAX_DEPARTURE_COUNT,
)
from .coordinator import HomeCoordinator, LineCoordinator, NswTrainsCoordinator
from .entity import NswTrainsEntity, home_device_info, line_device_info, station_device_info
from .passing import PredictedPass, next_pass

# Upcoming trackwork listed on the alerts sensor
MAX_UPCOMING_TRACKWORK = 5


def departure_attributes(departure: Departure | None, now: datetime | None = None) -> dict[str, Any]:
    if departure is None:
        return {}
    now = now or datetime.now(timezone.utc)
    return {
        "line": departure.line,
        "line_name": departure.line_name,
        "destination": departure.destination,
        "platform": departure.platform,
        "scheduled": departure.planned.isoformat(),
        "estimated": departure.estimated.isoformat() if departure.estimated else None,
        "delay_minutes": departure.delay_minutes,
        "realtime": departure.realtime,
        "due_in_minutes": max(0, int((departure.departure_time - now).total_seconds() // 60)),
    }


def unique_alerts(departures: list[Departure]) -> list[Alert]:
    """Alerts attached to any departure, once each, in the order they first appear."""
    seen: dict[str, Alert] = {}
    for departure in departures:
        for alert in departure.alerts:
            seen.setdefault(alert.id, alert)
    return list(seen.values())


def line_alert_attributes(alert: LineAlert, now: datetime) -> dict[str, Any]:
    window = alert.current_window(now)
    start = window[0] if window else alert.next_start(now)
    end = window[1] if window else next((e for s, e in alert.windows if s == start), None)
    return {
        "title": alert.title,
        "type": alert.announcement_type,
        "priority": alert.priority,
        "start": start.isoformat() if start else None,
        # Standing notices run "until" the year 2500; leave the end out rather than show that
        "end": end.isoformat() if end and end.year < 2100 else None,
        "url": alert.url,
    }


def pass_attributes(predicted: PredictedPass | None, now: datetime | None = None) -> dict[str, Any]:
    if predicted is None:
        return {}
    now = now or datetime.now(timezone.utc)
    return {
        "line": predicted.line,
        "destination": predicted.destination,
        "delay_minutes": predicted.delay_minutes,
        "realtime": predicted.realtime,
        "due_in_minutes": max(0, int((predicted.time - now).total_seconds() // 60)),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entry_type = entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_STATION)
    entities: list[SensorEntity]

    if entry_type == ENTRY_TYPE_LINE:
        line_coordinator: LineCoordinator = entry.runtime_data
        line = entry.data[CONF_LINE]
        entities = [
            LineStatusSensor(line_coordinator, line, entry.title),
            OnTimeSensor(line_coordinator, line, entry.title),
            LineAlertsSensor(line_coordinator, line, entry.title),
            NextTrackworkSensor(line_coordinator, line, entry.title),
        ]
    elif entry_type == ENTRY_TYPE_HOME:
        home_coordinator: HomeCoordinator = entry.runtime_data
        entities = [
            NextPassSensor(home_coordinator, entry.entry_id, entry.title, home_coordinator.station_a.name),
            NextPassSensor(home_coordinator, entry.entry_id, entry.title, home_coordinator.station_b.name),
            NearestTrainSensor(home_coordinator, entry.entry_id, entry.title),
        ]
    else:
        coordinator: NswTrainsCoordinator = entry.runtime_data
        stop_id = entry.data[CONF_STOP_ID]
        count = min(int(entry.options.get(CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT)), MAX_DEPARTURE_COUNT)
        entities = [DepartureSensor(coordinator, stop_id, entry.title, index) for index in range(count)]
        entities.append(AlertsSensor(coordinator, stop_id, entry.title))

    async_add_entities(entities)


# --- Station ---


class DepartureSensor(NswTrainsEntity[NswTrainsCoordinator], SensorEntity):
    """The Nth upcoming departure. The state is when it leaves (real-time when available)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:train"
    # Changes every minute; not worth keeping in history
    _unrecorded_attributes = frozenset({"due_in_minutes"})

    def __init__(self, coordinator: NswTrainsCoordinator, stop_id: str, stop_name: str, index: int) -> None:
        super().__init__(coordinator, station_device_info(stop_id, stop_name))
        self._index = index
        self._attr_unique_id = f"{stop_id}_departure_{index + 1}"
        self._attr_name = "Next departure" if index == 0 else f"Departure {index + 1}"

    @property
    def _departure(self) -> Departure | None:
        departures = self.coordinator.data or []
        return departures[self._index] if self._index < len(departures) else None

    @property
    def native_value(self) -> datetime | None:
        departure = self._departure
        return departure.departure_time if departure else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return departure_attributes(self._departure)


class AlertsSensor(NswTrainsEntity[NswTrainsCoordinator], SensorEntity):
    """Number of service alerts affecting upcoming departures, with the alerts as attributes."""

    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator: NswTrainsCoordinator, stop_id: str, stop_name: str) -> None:
        super().__init__(coordinator, station_device_info(stop_id, stop_name))
        self._attr_unique_id = f"{stop_id}_alerts"
        self._attr_name = "Service alerts"

    @property
    def native_value(self) -> int:
        return len(unique_alerts(self.coordinator.data or []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "alerts": [
                {"title": alert.title, "priority": alert.priority, "url": alert.url}
                for alert in unique_alerts(self.coordinator.data or [])
            ]
        }


# --- Line ---


class LineStatusSensor(NswTrainsEntity[LineCoordinator], SensorEntity):
    """Overall line status: good service, trackwork, delays or disruption."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = LINE_STATUSES
    _attr_translation_key = "line_status"
    _attr_icon = "mdi:train-variant"

    def __init__(self, coordinator: LineCoordinator, line: str, line_name: str) -> None:
        super().__init__(coordinator, line_device_info(line, line_name))
        self._attr_unique_id = f"line_{line}_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.status if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.data
        return {"reason": status.reason} if status else {}


class OnTimeSensor(NswTrainsEntity[LineCoordinator], SensorEntity):
    """Share of trains running now that are on time, with the late ones as attributes."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: LineCoordinator, line: str, line_name: str) -> None:
        super().__init__(coordinator, line_device_info(line, line_name))
        self._attr_unique_id = f"line_{line}_on_time"
        self._attr_name = "On time"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.on_time_percent if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.data
        if not status:
            return {}
        return {
            "trains_running": len(status.running),
            "late_trains": [{"train": train.label, "delay_minutes": train.delay_minutes} for train in status.late],
            "on_time_threshold_minutes": status.on_time_minutes,
            "average_delay_minutes": status.average_delay_minutes,
            "max_delay_minutes": status.max_delay_minutes,
            "cancelled_trips": status.cancelled_trips,
        }


class LineAlertsSensor(NswTrainsEntity[LineCoordinator], SensorEntity):
    """Alerts in effect on the line now, as shown on transportnsw.info, plus upcoming trackwork."""

    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator: LineCoordinator, line: str, line_name: str) -> None:
        super().__init__(coordinator, line_device_info(line, line_name))
        self._attr_unique_id = f"line_{line}_alerts"
        self._attr_name = "Alerts"

    @property
    def native_value(self) -> int | None:
        return len(self.coordinator.data.active_alerts) if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.data
        if not status:
            return {}
        now = datetime.now(timezone.utc)
        return {
            "alerts": [line_alert_attributes(alert, now) for alert in status.active_alerts],
            "upcoming_trackwork": [
                line_alert_attributes(alert, now) for alert in status.upcoming_trackwork[:MAX_UPCOMING_TRACKWORK]
            ],
        }


class NextTrackworkSensor(NswTrainsEntity[LineCoordinator], SensorEntity):
    """When the next planned trackwork starts."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:hammer-wrench"

    def __init__(self, coordinator: LineCoordinator, line: str, line_name: str) -> None:
        super().__init__(coordinator, line_device_info(line, line_name))
        self._attr_unique_id = f"line_{line}_next_trackwork"
        self._attr_name = "Next trackwork"

    @property
    def _next(self) -> LineAlert | None:
        status = self.coordinator.data
        return status.upcoming_trackwork[0] if status and status.upcoming_trackwork else None

    @property
    def native_value(self) -> datetime | None:
        alert = self._next
        return alert.next_start(datetime.now(timezone.utc)) if alert else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alert = self._next
        return line_alert_attributes(alert, datetime.now(timezone.utc)) if alert else {}


# --- Home ---


class NextPassSensor(NswTrainsEntity[HomeCoordinator], SensorEntity):
    """When the next train heading towards a station passes the house."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:train"
    _unrecorded_attributes = frozenset({"due_in_minutes"})

    def __init__(self, coordinator: HomeCoordinator, entry_id: str, title: str, towards: str) -> None:
        super().__init__(coordinator, home_device_info(entry_id, title))
        self._towards = towards
        short_name = towards.split(",")[0].removesuffix(" Station")
        self._attr_unique_id = f"{entry_id}_next_towards_{short_name.lower().replace(' ', '_')}"
        self._attr_name = f"Next train towards {short_name}"

    @property
    def _next(self) -> PredictedPass | None:
        data = self.coordinator.data
        return next_pass(data.passes, self._towards, datetime.now(timezone.utc)) if data else None

    @property
    def native_value(self) -> datetime | None:
        predicted = self._next
        return predicted.time if predicted else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return pass_attributes(self._next)


class NearestTrainSensor(NswTrainsEntity[HomeCoordinator], SensorEntity):
    """Distance to the closest train by GPS. Its latitude/longitude attributes put it on the map card."""

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = "m"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:map-marker-distance"
    _unrecorded_attributes = frozenset({"latitude", "longitude", "position_age_seconds"})

    def __init__(self, coordinator: HomeCoordinator, entry_id: str, title: str) -> None:
        super().__init__(coordinator, home_device_info(entry_id, title))
        self._attr_unique_id = f"{entry_id}_nearest_train"
        self._attr_name = "Nearest train"

    @property
    def native_value(self) -> int | None:
        nearest = self.coordinator.data.nearest if self.coordinator.data else None
        return round(nearest.distance_m) if nearest else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        nearest = self.coordinator.data.nearest if self.coordinator.data else None
        if nearest is None:
            return {}
        vehicle = nearest.vehicle
        return {
            "train": vehicle.label,
            "line": vehicle.line,
            "latitude": vehicle.latitude,
            "longitude": vehicle.longitude,
            "position_age_seconds": int((datetime.now(timezone.utc) - vehicle.timestamp).total_seconds()),
        }
