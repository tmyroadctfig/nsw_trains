"""Unit tests for the shared feed cache."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.nsw_trains.feeds import FeedCache, get_feed_cache


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.mark.unit
async def test_reuses_fresh_value():
    clock = Clock()
    cache = FeedCache(clock)
    fetch = AsyncMock(side_effect=["first", "second"])

    assert await cache.async_get("feed", timedelta(seconds=30), fetch) == "first"
    clock.now += 29
    assert await cache.async_get("feed", timedelta(seconds=30), fetch) == "first"
    clock.now += 2
    assert await cache.async_get("feed", timedelta(seconds=30), fetch) == "second"
    assert fetch.await_count == 2


@pytest.mark.unit
async def test_keys_are_independent():
    cache = FeedCache(Clock())
    assert await cache.async_get("a", timedelta(minutes=1), AsyncMock(return_value=1)) == 1
    assert await cache.async_get("b", timedelta(minutes=1), AsyncMock(return_value=2)) == 2


@pytest.mark.unit
async def test_concurrent_callers_share_one_fetch():
    cache = FeedCache(Clock())
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "value"

    results = await asyncio.gather(*(cache.async_get("feed", timedelta(seconds=30), fetch) for _ in range(5)))

    assert results == ["value"] * 5
    assert calls == 1


@pytest.mark.unit
async def test_failed_fetch_is_not_cached():
    cache = FeedCache(Clock())
    fetch = AsyncMock(side_effect=[RuntimeError("down"), "ok"])

    with pytest.raises(RuntimeError):
        await cache.async_get("feed", timedelta(seconds=30), fetch)
    assert await cache.async_get("feed", timedelta(seconds=30), fetch) == "ok"


@pytest.mark.unit
def test_one_cache_per_home_assistant():
    hass = MagicMock()
    hass.data = {}
    assert get_feed_cache(hass) is get_feed_cache(hass)
