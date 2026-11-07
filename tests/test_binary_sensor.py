"""Unit tests for the train passing binary sensor."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.nsw_trains.binary_sensor import TrainPassingSensor, async_setup_entry
from custom_components.nsw_trains.const import CONF_ENTRY_TYPE, CONF_PASSING_RADIUS, ENTRY_TYPE_HOME, ENTRY_TYPE_LINE
from custom_components.nsw_trains.coordinator import HomeData
from custom_components.nsw_trains.gtfs import VehiclePosition
from custom_components.nsw_trains.passing import NearbyTrain, PredictedPass

NOW = datetime.now(timezone.utc)


def make_sensor(passes=(), nearest=None, radius=300):
    coordinator = MagicMock()
    coordinator.data = HomeData(passes=list(passes), nearest=nearest)
    return TrainPassingSensor(coordinator, "entry", "Home", radius)


def predicted(seconds_from_now):
    return PredictedPass(NOW + timedelta(seconds=seconds_from_now), "Thirroul", "SCO", "Kiama", 0, True, "trip")


def nearby(distance, age_seconds):
    vehicle = VehiclePosition("t", "SCO", "Train", -34.3, 150.9, NOW - timedelta(seconds=age_seconds))
    return NearbyTrain(vehicle, distance)


@pytest.mark.unit
def test_on_when_predicted_pass_is_now():
    sensor = make_sensor(passes=[predicted(20)])

    assert sensor.is_on
    assert sensor.extra_state_attributes == {"source": "prediction", "towards": "Thirroul", "line": "SCO"}


@pytest.mark.unit
def test_off_when_predicted_pass_is_minutes_away():
    sensor = make_sensor(passes=[predicted(5 * 60), predicted(-5 * 60)])

    assert not sensor.is_on
    assert sensor.extra_state_attributes == {}


@pytest.mark.unit
def test_on_when_fresh_gps_position_is_close():
    sensor = make_sensor(nearest=nearby(200, age_seconds=30))

    assert sensor.is_on
    assert sensor.extra_state_attributes == {"source": "gps"}


@pytest.mark.unit
@pytest.mark.parametrize("distance, age", [(400, 30), (200, 200)])
def test_off_when_gps_too_far_or_stale(distance, age):
    assert not make_sensor(nearest=nearby(distance, age_seconds=age)).is_on


@pytest.mark.unit
def test_radius_is_configurable():
    assert make_sensor(nearest=nearby(400, age_seconds=30), radius=500).is_on


@pytest.mark.unit
def test_off_without_data():
    sensor = make_sensor()
    sensor.coordinator.data = None
    assert not sensor.is_on


@pytest.mark.unit
async def test_rechecks_between_updates():
    """A predicted pass time arrives without new data, so the state is re-evaluated on a timer."""
    sensor = make_sensor()
    sensor.hass = MagicMock()

    await sensor.async_added_to_hass()
    assert len(sensor.removers) == 1

    sensor._recheck(NOW)
    assert sensor.state_writes == 1


@pytest.mark.unit
@pytest.mark.parametrize("entry_type, count", [(ENTRY_TYPE_HOME, 1), (ENTRY_TYPE_LINE, 0), (None, 0)])
async def test_only_home_entries_get_the_sensor(entry_type, count):
    entry = MagicMock()
    entry.data = {CONF_ENTRY_TYPE: entry_type} if entry_type else {}
    entry.options = {CONF_PASSING_RADIUS: 500}
    add_entities = MagicMock()

    await async_setup_entry(MagicMock(), entry, add_entities)

    assert add_entities.call_count == count
    if count:
        assert add_entities.call_args.args[0][0]._radius_m == 500
