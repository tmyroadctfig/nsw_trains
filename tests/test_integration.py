"""Integration tests against the live Transport for NSW API. Needs TFNSW_API_KEY to be set."""

import os

import aiohttp
import pytest

from custom_components.nsw_trains.api import TransportNswClient

API_KEY = os.environ.get("TFNSW_API_KEY")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not API_KEY, reason="TFNSW_API_KEY not set"),
]


async def test_find_central_station():
    async with aiohttp.ClientSession() as session:
        stops = await TransportNswClient(session, API_KEY or "").async_find_stops("Central Station")

    assert any("Central" in stop.name for stop in stops)


async def test_central_station_departures():
    async with aiohttp.ClientSession() as session:
        client = TransportNswClient(session, API_KEY or "")
        stop = (await client.async_find_stops("Central Station"))[0]
        departures = await client.async_get_departures(stop.id)

    assert departures, "Central always has upcoming trains"
    assert all(d.product_class in (1, 2) for d in departures)
    assert all(d.platform and d.platform.isalnum() for d in departures), [d.platform for d in departures]
    times = [d.departure_time for d in departures]
    assert times == sorted(times)


async def test_stations_near_austinmer():
    async with aiohttp.ClientSession() as session:
        stations = await TransportNswClient(session, API_KEY or "").async_find_stations_near(-34.305, 150.929, 8000)

    names = [station.name for station in stations]
    assert names[0] == "Austinmer Station, Austinmer"
    assert {"Thirroul Station, Thirroul", "Coledale Station, Coledale"} <= set(names)
    assert all(station.latitude and station.longitude for station in stations)


async def test_line_alerts():
    async with aiohttp.ClientSession() as session:
        alerts = await TransportNswClient(session, API_KEY or "").async_get_line_alerts()

    lines = set().union(*(alert.lines for alert in alerts))
    # Alerts are always current for some of the big lines, e.g. standing "platforms may change" notices
    assert lines & {"T1", "T4", "SCO"}
    assert all(alert.windows for alert in alerts)


async def test_realtime_feeds():
    async with aiohttp.ClientSession() as session:
        client = TransportNswClient(session, API_KEY or "")
        vehicles = await client.async_get_vehicle_positions()
        delays = await client.async_get_trip_delays()

    assert vehicles, "Trains are always on the network"
    assert delays
    # Vehicle trip ids match trip update ids, which is what line status relies on
    assert {v.trip_id for v in vehicles} & set(delays)


async def test_departures_from_before_now_include_departed_trains():
    """The home prediction asks for departures from 15 minutes ago; the API must honour a past time."""
    from datetime import datetime, timedelta, timezone

    async with aiohttp.ClientSession() as session:
        client = TransportNswClient(session, API_KEY or "")
        earlier = datetime.now(timezone.utc) - timedelta(minutes=15)
        departures = await client.async_get_departures("200060", include_metro=False, now=earlier)

    assert departures
    assert min(d.planned for d in departures) < datetime.now(timezone.utc)
    assert all(d.trip_id for d in departures)
