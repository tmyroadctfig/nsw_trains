"""Works out how a line is running from its alerts and live train delays."""

from dataclasses import dataclass, field
from datetime import datetime

from .api import LineAlert
from .const import (
    INTERCITY_LINES,
    INTERCITY_ON_TIME_MINUTES,
    LINE_STATUS_DELAYS,
    LINE_STATUS_DISRUPTION,
    LINE_STATUS_GOOD,
    LINE_STATUS_TRACKWORK,
    ON_TIME_MINUTES,
    VEHICLE_MAX_AGE,
)
from .gtfs import TripDelay, VehiclePosition

# High priority alerts mean a disruption; Transport for NSW uses these for incidents rather than planned work
DISRUPTION_PRIORITIES = frozenset({"high", "veryHigh"})
TRACKWORK = "trackwork"
# Per-trip notices such as "Running late" or "Not stopping at". Some are left open for years, and live delays already
# cover lateness, so they are left out of the line's alerts.
TRIP_MESSAGE = "tripMessage"
# The line has delays when fewer trains than this are on time, or any is this late
MIN_ON_TIME_PERCENT = 80
MAJOR_DELAY_MINUTES = 15


@dataclass(frozen=True)
class RunningTrain:
    trip_id: str
    label: str
    delay_minutes: float


@dataclass
class LineStatus:
    status: str
    reason: str
    on_time_minutes: int
    running: list[RunningTrain] = field(default_factory=list)
    cancelled_trips: int = 0
    active_alerts: list[LineAlert] = field(default_factory=list)
    upcoming_trackwork: list[LineAlert] = field(default_factory=list)

    @property
    def late(self) -> list[RunningTrain]:
        return [train for train in self.running if train.delay_minutes >= self.on_time_minutes]

    @property
    def on_time_percent(self) -> float | None:
        if not self.running:
            return None
        return round(100 * (len(self.running) - len(self.late)) / len(self.running), 1)

    @property
    def max_delay_minutes(self) -> float | None:
        return max((train.delay_minutes for train in self.running), default=None)

    @property
    def average_delay_minutes(self) -> float | None:
        if not self.running:
            return None
        return round(sum(train.delay_minutes for train in self.running) / len(self.running), 1)


def on_time_minutes(line: str) -> int:
    return INTERCITY_ON_TIME_MINUTES if line in INTERCITY_LINES else ON_TIME_MINUTES


def running_trains(
    line: str, vehicles: list[VehiclePosition], delays: dict[str, TripDelay], now: datetime
) -> list[RunningTrain]:
    """Trains on the line with a recent GPS position and a live delay, most delayed first."""
    trains = []
    for vehicle in vehicles:
        if vehicle.line != line or now - vehicle.timestamp > VEHICLE_MAX_AGE:
            continue
        delay = delays.get(vehicle.trip_id)
        if delay is None or delay.cancelled or delay.delay_seconds is None:
            continue
        trains.append(RunningTrain(vehicle.trip_id, vehicle.label, round(delay.delay_seconds / 60, 1)))
    trains.sort(key=lambda train: train.delay_minutes, reverse=True)
    return trains


def compute_line_status(
    line: str,
    alerts: list[LineAlert],
    vehicles: list[VehiclePosition],
    delays: dict[str, TripDelay],
    now: datetime,
) -> LineStatus:
    """Overall status, most serious first: disruption, delays, trackwork, then good service."""
    line_alerts = [alert for alert in alerts if line in alert.lines and alert.info_type != TRIP_MESSAGE]
    active = [alert for alert in line_alerts if alert.is_active(now)]
    upcoming = sorted(
        (alert for alert in line_alerts if alert.announcement_type == TRACKWORK and alert.next_start(now) is not None),
        key=lambda alert: alert.next_start(now) or now,
    )

    status = LineStatus(
        status=LINE_STATUS_GOOD,
        reason="Trains are running normally",
        on_time_minutes=on_time_minutes(line),
        running=running_trains(line, vehicles, delays, now),
        cancelled_trips=sum(1 for delay in delays.values() if delay.line == line and delay.cancelled),
        active_alerts=active,
        upcoming_trackwork=upcoming,
    )

    disruptions = [alert for alert in active if alert.priority in DISRUPTION_PRIORITIES]
    trackwork = [alert for alert in active if alert.announcement_type == TRACKWORK]
    on_time = status.on_time_percent
    max_delay = status.max_delay_minutes

    if disruptions:
        status.status, status.reason = LINE_STATUS_DISRUPTION, disruptions[0].title
    elif on_time is not None and (on_time < MIN_ON_TIME_PERCENT or (max_delay or 0) >= MAJOR_DELAY_MINUTES):
        status.status = LINE_STATUS_DELAYS
        status.reason = (
            f"{len(status.late)} of {len(status.running)} trains running {status.on_time_minutes}+ minutes late, "
            f"up to {round(max_delay or 0)} minutes"
        )
    elif trackwork:
        status.status, status.reason = LINE_STATUS_TRACKWORK, trackwork[0].title
    return status
