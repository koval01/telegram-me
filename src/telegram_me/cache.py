"""Key-value cache with Redis and in-memory backends.

Entries remember when they were stored, so callers can apply their own
freshness rules (for example stale-while-revalidate) on top of the
backend expiry. The Redis backend never lets an outage slow requests
down: commands use short timeouts and a circuit breaker skips Redis for
a while after a failure.
"""

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from telegram_me.settings import Settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "tme:v2:"


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A cached value and the wall-clock time it was stored at."""

    value: str
    stored_at: float

    @property
    def age(self) -> float:
        """Seconds since the entry was stored."""
        return time.time() - self.stored_at


class Cache(Protocol):
    """Asynchronous string cache."""

    @property
    def backend(self) -> str:
        """Short backend name for logs and diagnostics."""
        ...

    async def get(self, key: str) -> CacheEntry | None:
        """Return the entry for `key`, or None when missing or unavailable."""
        ...

    async def set(self, key: str, value: str, ttl: float) -> None:
        """Store `value` under `key` for `ttl` seconds (best effort)."""
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...


class MemoryCache:
    """In-process LRU cache with per-entry expiry, bounded by total value size."""

    backend = "memory"

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._size = 0
        self._data: OrderedDict[str, tuple[CacheEntry, float]] = OrderedDict()

    def _drop(self, key: str) -> None:
        entry, _ = self._data.pop(key)
        self._size -= len(entry.value)

    async def get(self, key: str) -> CacheEntry | None:  # noqa: D102
        item = self._data.get(key)
        if item is None:
            return None
        entry, expires_at = item
        if expires_at <= time.monotonic():
            self._drop(key)
            return None
        self._data.move_to_end(key)
        return entry

    async def set(self, key: str, value: str, ttl: float) -> None:  # noqa: D102
        if ttl <= 0 or len(value) > self._max_bytes:
            return
        if key in self._data:
            self._drop(key)
        self._data[key] = (CacheEntry(value, time.time()), time.monotonic() + ttl)
        self._size += len(value)
        while self._size > self._max_bytes:
            self._drop(next(iter(self._data)))

    async def close(self) -> None:  # noqa: D102
        self._data.clear()
        self._size = 0


class RedisCache:
    """Redis-backed cache that degrades to "always miss" while Redis is down."""

    backend = "redis"

    def __init__(self, client: Redis, cooldown: float = 30.0) -> None:
        self.client = client
        self._cooldown = cooldown
        self._disabled_until = 0.0

    @property
    def available(self) -> bool:
        """False while the circuit breaker is open after a Redis failure."""
        return time.monotonic() >= self._disabled_until

    def trip(self, exc: Exception) -> None:
        """Open the circuit breaker after a failed Redis command."""
        if self.available:
            logger.warning("Redis unavailable, bypassing it for %.0fs: %s", self._cooldown, exc)
        self._disabled_until = time.monotonic() + self._cooldown

    async def get(self, key: str) -> CacheEntry | None:  # noqa: D102
        if not self.available:
            return None
        try:
            raw = await self.client.get(KEY_PREFIX + key)
        except (RedisError, OSError) as exc:
            self.trip(exc)
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        stored_at, separator, value = raw.partition("\n")
        try:
            return CacheEntry(value, float(stored_at)) if separator else None
        except ValueError:
            return None

    async def set(self, key: str, value: str, ttl: float) -> None:  # noqa: D102
        if ttl <= 0 or not self.available:
            return
        try:
            await self.client.set(
                KEY_PREFIX + key, f"{time.time():.3f}\n{value}", px=int(ttl * 1000)
            )
        except (RedisError, OSError) as exc:
            self.trip(exc)

    async def close(self) -> None:  # noqa: D102
        await self.client.aclose()


def create_redis(settings: Settings) -> Redis | None:
    """Build a Redis client with short timeouts, or None when REDIS_URL is unset."""
    if not settings.redis_url:
        return None
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.redis_timeout,
        socket_connect_timeout=settings.redis_timeout,
        health_check_interval=30,
    )


def create_cache(settings: Settings, redis: Redis | None) -> Cache:
    """Pick the cache backend for the given settings."""
    if redis is None:
        return MemoryCache(settings.memory_cache_max_mb * 1024 * 1024)
    return RedisCache(redis)
