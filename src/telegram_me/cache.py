"""Key-value cache with Redis and in-memory backends.

Entries remember when they were stored, so callers can apply their own
freshness rules (for example stale-while-revalidate) on top of the
backend expiry. The Redis backend never lets an outage slow requests
down: commands use short timeouts and a circuit breaker skips Redis for
a while after a failure.

Values are UTF-8 bytes (JSON documents). As `str`, a page with a single
emoji would take four bytes per character, and the response would have to
be encoded again on every cache hit.
"""

import logging
import sys
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.client import NEVER_DECODE
from redis.exceptions import RedisError

from telegram_me.settings import Settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "tme:v2:"

_compress: Callable[[bytes], bytes]
_decompress: Callable[[bytes], bytes]
if sys.version_info >= (3, 14):
    from compression import zstd

    # Level 1: ~4x smaller rendered pages; decompressing one is faster than
    # encoding the same JSON from `str` to UTF-8 was.
    def _compress(data: bytes) -> bytes:
        return zstd.compress(data, 1)

    _decompress = zstd.decompress
else:  # pragma: no cover - Python 3.13 has no zstd in the standard library

    def _compress(data: bytes) -> bytes:
        return zlib.compress(data, 1)

    _decompress = zlib.decompress

# Approximate memory of one MemoryCache entry besides its key and value:
# the OrderedDict slot and link node, the tuple and two floats (measured
# with tracemalloc: ~230 bytes).
_ENTRY_OVERHEAD = 240


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A cached value and the wall-clock time it was stored at."""

    value: bytes
    stored_at: float

    @property
    def age(self) -> float:
        """Seconds since the entry was stored."""
        return time.time() - self.stored_at


class Cache(Protocol):
    """Asynchronous bytes cache."""

    @property
    def backend(self) -> str:
        """Short backend name for logs and diagnostics."""
        ...

    async def get(self, key: str) -> CacheEntry | None:
        """Return the entry for `key`, or None when missing or unavailable."""
        ...

    async def set(self, key: str, value: bytes, ttl: float) -> None:
        """Store `value` under `key` for `ttl` seconds (best effort)."""
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...


class MemoryCache:
    """In-process LRU cache with per-entry expiry, bounded by memory use.

    Values are kept compressed (zstd, or zlib before Python 3.14), and the
    bound counts what entries really occupy: compressed value, key and
    bookkeeping.
    """

    backend = "memory"

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._size = 0
        # key -> (compressed value, stored at (wall clock), expires at (monotonic))
        self._data: OrderedDict[str, tuple[bytes, float, float]] = OrderedDict()

    @property
    def size(self) -> int:
        """Approximate bytes held by the entries."""
        return self._size

    def __len__(self) -> int:
        return len(self._data)

    @staticmethod
    def _footprint(key: str, blob: bytes) -> int:
        return sys.getsizeof(key) + sys.getsizeof(blob) + _ENTRY_OVERHEAD

    def _drop(self, key: str) -> None:
        blob, _, _ = self._data.pop(key)
        self._size -= self._footprint(key, blob)

    async def get(self, key: str) -> CacheEntry | None:  # noqa: D102
        item = self._data.get(key)
        if item is None:
            return None
        blob, stored_at, expires_at = item
        if expires_at <= time.monotonic():
            self._drop(key)
            return None
        self._data.move_to_end(key)
        return CacheEntry(_decompress(blob), stored_at)

    async def set(self, key: str, value: bytes, ttl: float) -> None:  # noqa: D102
        if ttl <= 0:
            return
        blob = _compress(value)
        footprint = self._footprint(key, blob)
        if footprint > self._max_bytes:
            return
        if key in self._data:
            self._drop(key)
        self._data[key] = (blob, time.time(), time.monotonic() + ttl)
        self._size += footprint
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
            # The client decodes replies to `str`; values are wanted as bytes.
            raw = await self.client.execute_command(  # type: ignore[no-untyped-call]
                "GET", KEY_PREFIX + key, **{NEVER_DECODE: True}
            )
        except (RedisError, OSError) as exc:
            self.trip(exc)
            return None
        if not isinstance(raw, bytes):
            return None
        stored_at, separator, value = raw.partition(b"\n")
        try:
            return CacheEntry(value, float(stored_at)) if separator else None
        except ValueError:
            return None

    async def set(self, key: str, value: bytes, ttl: float) -> None:  # noqa: D102
        if ttl <= 0 or not self.available:
            return
        try:
            await self.client.set(
                KEY_PREFIX + key, b"%.3f\n%b" % (time.time(), value), px=int(ttl * 1000)
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
