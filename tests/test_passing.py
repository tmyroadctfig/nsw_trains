"""Unit tests for predicting trains passing a point, and finding the nearest train."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.nsw_trains.api import Departure
from custom_components.nsw_trains.gtfs import VehiclePosition
from custom_components.nsw_trains.passing import (
    distance_m,
    fraction_along,
    is_passing,
    nearest_train,
    next_pass,
    predict_passes,
)

NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
AUSTINMER = (-34.306457, 150.929233)
THIRROUL = (-34.318016, 150.918912)


def at(minutes, seconds=0):
    return NOW + timedelta(minutes=minutes, seconds=seconds)


def departure(trip_id, minutes, estimated_minutes=None, destination="Kiama"):
    estimated = at(estimated_minutes) if estimated_minutes is not None else None
    return Departure(
        "SCO", "South Coast Line", destination, "1", at(minutes), estimated, estimated is not None, 1, (), trip_id
    )


@pytest.mark.unit
def test_distance_between_austinmer_and_thirroul():
    assert distance_m(*AUSTINMER, *THIRROUL) == pytest.approx(1580, abs=20)


@pytest.mark.unit
def test_fraction_along():
    midpoint = ((AUSTINMER[0] + THIRROUL[0]) / 2, (AUSTINMER[1] + THIRROUL[1]) / 2)

    assert fraction_along(AUSTINMER, THIRROUL, AUSTINMER) == pytest.approx(0)
    assert fraction_along(AUSTINMER, THIRROUL, THIRROUL) == pytest.approx(1)
    assert fraction_along(AUSTINMER, THIRROUL, midpoint) == pytest.approx(0.5)


@pytest.mark.unit
def test_fraction_along_is_clamped_beyond_the_stations():
    beyond_thirroul = (THIRROUL[0] - 0.01, THIRROUL[1] - 0.01)
    assert fraction_along(AUSTINMER, THIRROUL, beyond_thirroul) == 1.0
    assert fraction_along(AUSTINMER, AUSTINMER, THIRROUL) == 0.0


@pytest.mark.unit
def test_predicts_both_directions():
    """Southbound leaves Austinmer first; northbound leaves Thirroul first."""
    austinmer = [departure("south", 10, destination="Kiama"), departure("north", 22, destination="Bondi Junction")]
    thirroul = [departure("south", 14, destination="Kiama"), departure("north", 20, destination="Bondi Junction")]

    passes = predict_passes(austinmer, thirroul, 0.25, "Austinmer", "Thirroul", since=NOW)

    assert [(p.trip_id, p.towards, p.time) for p in passes] == [
        # A quarter of the way from Austinmer: 10 + 0.25 * 4 minutes
        ("south", "Thirroul", at(11)),
        # Three quarters of the way from Thirroul: 20 + 0.75 * 2 minutes
        ("north", "Austinmer", at(21, 30)),
    ]
    assert passes[1].destination == "Bondi Junction"


@pytest.mark.unit
def test_uses_real_time_estimates():
    austinmer = [departure("late", 10, estimated_minutes=17)]
    thirroul = [departure("late", 14, estimated_minutes=21)]

    [predicted] = predict_passes(austinmer, thirroul, 0.5, "Austinmer", "Thirroul", since=NOW)

    assert predicted.time == at(19)
    assert predicted.delay_minutes == 7
    assert predicted.realtime is True


@pytest.mark.unit
def test_trains_stopping_at_only_one_station_are_skipped():
    austinmer = [departure("both", 10), departure("only-a", 12), Departure("SCO", "", "", None, at(5), None, False, 1)]
    thirroul = [departure("both", 14), departure("only-b", 16)]

    passes = predict_passes(austinmer, thirroul, 0.5, "Austinmer", "Thirroul", since=NOW)

    assert [p.trip_id for p in passes] == ["both"]


@pytest.mark.unit
def test_passes_before_since_are_dropped():
    austinmer = [departure("gone", -10), departure("recent", -2)]
    thirroul = [departure("gone", -6), departure("recent", 2)]

    passes = predict_passes(austinmer, thirroul, 0.5, "Austinmer", "Thirroul", since=at(-1))

    assert [p.trip_id for p in passes] == ["recent"]


@pytest.mark.unit
def test_next_pass_by_direction():
    austinmer = [departure("s1", -2), departure("s2", 30), departure("n1", 12)]
    thirroul = [departure("s1", 2), departure("s2", 34), departure("n1", 10)]
    passes = predict_passes(austinmer, thirroul, 0.5, "Austinmer", "Thirroul", since=at(-5))

    # s1 passes at the moment NOW, so it's still the next one southbound; it drops off after
    assert next_pass(passes, "Thirroul", NOW).trip_id == "s1"
    assert next_pass(passes, "Thirroul", at(1)).trip_id == "s2"
    assert next_pass(passes, "Austinmer", NOW).trip_id == "n1"
    assert next_pass(passes, "Austinmer", at(20)) is None


@pytest.mark.unit
def test_is_passing_window():
    passes = predict_passes([departure("s", 9)], [departure("s", 11)], 0.5, "A", "B", since=NOW)

    assert is_passing(passes, at(10), timedelta(seconds=60)).trip_id == "s"
    assert is_passing(passes, at(10, 59), timedelta(seconds=60)) is not None
    assert is_passing(passes, at(11, 1), timedelta(seconds=60)) is None
    assert is_passing(passes, at(8, 59), timedelta(seconds=60)) is None


@pytest.mark.unit
def test_nearest_train_ignores_stale_positions():
    vehicles = [
        VehiclePosition("far", "SCO", "Far", -34.4, 150.85, NOW - timedelta(seconds=20)),
        VehiclePosition("close-stale", "SCO", "Stale", *AUSTINMER, NOW - timedelta(hours=3)),
        VehiclePosition("close", "SCO", "Close", -34.31, 150.925, NOW - timedelta(seconds=40)),
    ]

    nearest = nearest_train(vehicles, *AUSTINMER, NOW, timedelta(minutes=5))

    assert nearest.vehicle.trip_id == "close"
    assert nearest.distance_m == pytest.approx(distance_m(*AUSTINMER, -34.31, 150.925))


@pytest.mark.unit
def test_nearest_train_none_when_nothing_fresh():
    vehicles = [VehiclePosition("stale", "SCO", "", *AUSTINMER, NOW - timedelta(hours=1))]
    assert nearest_train(vehicles, *AUSTINMER, NOW, timedelta(minutes=5)) is None
    assert nearest_train([], *AUSTINMER, NOW, timedelta(minutes=5)) is None


@pytest.mark.unit
def test_pass_times_are_whole_seconds():
    [predicted] = predict_passes([departure("s", 10)], [departure("s", 11)], 1 / 3, "A", "B", since=NOW)
    assert predicted.time == at(10, 20)
