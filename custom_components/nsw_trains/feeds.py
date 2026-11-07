"""Shares network-wide feeds between config entries so each is fetched once, however many entries use it."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

from homeassistant.core import HomeAssistant

from .const import DOMAIN

T = TypeVar("T")

FEED_CACHE_KEY = "feed_cache"


class FeedCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def async_get(self, key: str, max_age: timedelta, fetch: Callable[[], Awaitable[T]]) -> T:
        """Return the cached value for key if fresh enough, otherwise fetch it. Concurrent callers share one fetch."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._entries.get(key)
            if cached is not None and self._clock() - cached[0] < max_age.total_seconds():
                value: T = cached[1]
                return value
            value = await fetch()
            self._entries[key] = (self._clock(), value)
            return value


def get_feed_cache(hass: HomeAssistant) -> FeedCache:
    data = hass.data.setdefault(DOMAIN, {})
    cache: FeedCache = data.setdefault(FEED_CACHE_KEY, FeedCache())
    return cache
