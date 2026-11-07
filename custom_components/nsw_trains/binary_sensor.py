"""Binary sensor platform: is a train passing the house right now?"""

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_ENTRY_TYPE,
    CONF_PASSING_RADIUS,
    DEFAULT_PASSING_RADIUS,
    ENTRY_TYPE_HOME,
    PASSING_GPS_MAX_AGE,
    PASSING_WINDOW,
)
from .coordinator import HomeCoordinator
from .entity import NswTrainsEntity, home_device_info
from .passing import PredictedPass, is_passing

# Re-check between coordinator updates, since a predicted pass time arrives without new data
RECHECK_INTERVAL = timedelta(seconds=10)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_HOME:
        return
    radius = int(entry.options.get(CONF_PASSING_RADIUS, DEFAULT_PASSING_RADIUS))
    async_add_entities([TrainPassingSensor(entry.runtime_data, entry.entry_id, entry.title, radius)])


class TrainPassingSensor(NswTrainsEntity[HomeCoordinator], BinarySensorEntity):
    """On while a train is passing: a predicted pass within a minute of now, or a fresh GPS position close by."""

    _attr_icon = "mdi:train-variant"

    def __init__(self, coordinator: HomeCoordinator, entry_id: str, title: str, radius_m: int) -> None:
        super().__init__(coordinator, home_device_info(entry_id, title))
        self._radius_m = radius_m
        self._attr_unique_id = f"{entry_id}_train_passing"
        self._attr_name = "Train passing"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._recheck, RECHECK_INTERVAL))

    @callback
    def _recheck(self, _now: datetime) -> None:
        self.async_write_ha_state()

    def _predicted(self, now: datetime) -> PredictedPass | None:
        data = self.coordinator.data
        return is_passing(data.passes, now, PASSING_WINDOW) if data else None

    def _gps_nearby(self, now: datetime) -> bool:
        nearest = self.coordinator.data.nearest if self.coordinator.data else None
        return (
            nearest is not None
            and nearest.distance_m <= self._radius_m
            and now - nearest.vehicle.timestamp <= PASSING_GPS_MAX_AGE
        )

    @property
    def is_on(self) -> bool:
        now = datetime.now(timezone.utc)
        return self._predicted(now) is not None or self._gps_nearby(now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        predicted = self._predicted(now)
        if predicted is not None:
            return {"source": "prediction", "towards": predicted.towards, "line": predicted.line}
        if self._gps_nearby(now):
            return {"source": "gps"}
        return {}
