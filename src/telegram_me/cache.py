"""Key-value cache: an in-process cache, in front of Redis when it is set.

Entries remember when they were stored, so callers can apply their own
freshness rules (for example stale-while-revalidate) on top of the
backend expiry. The Redis backend never lets an outage slow requests
down: commands use short timeouts and a circuit breaker skips Redis for
a while after a failure. A burst of commands waits briefly for a pooled
connection instead; an exhausted pool is a local limit, not an outage.

The in-process cache is native (`telegram_me_native.LocalCache`, Rust on
`moka`). With Redis it is a first level that answers repeated requests
without a network round trip (`TwoLevelCache`); without Redis it is the only
level (`MemoryCache`).

Values are UTF-8 bytes (JSON documents). As `str`, a page with a single
emoji would take four bytes per character, and the response would have to
be encoded again on every cache hit.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.connection import Connection
from redis.client import NEVER_DECODE
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import MaxConnectionsError, RedisError
from telegram_me_native import LocalCache

from telegram_me.services.coalesce import SingleFlight
from telegram_me.settings import Settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "tme:v2:"

# Redis connections per worker: a feed or previews request runs up to 100
# commands at once, so the pool is sized for upstream fetches finishing
# together (two writes each) and bursts beyond it wait for a free connection.
_MIN_REDIS_CONNECTIONS = 32


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

    async def set(
        self, key: str, value: bytes, ttl: float, *, stored_at: float | None = None
    ) -> None:
        """Store `value` under `key` for `ttl` seconds (best effort).

        `stored_at` (default: now) is the time the value counts as fresh from,
        for a value derived from an older one.
        """
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...


class MemoryCache:
    """In-process cache with per-entry expiry, bounded by memory use.

    A thin asynchronous face of the native `LocalCache`: least recently used
    entries are evicted first, values are kept zstd-compressed, and the bound
    counts what entries really occupy (compressed value, key and bookkeeping).
    """

    backend = "memory"

    def __init__(self, max_bytes: int) -> None:
        self._native = LocalCache(max_bytes)

    @property
    def size(self) -> int:
        """Bytes held by the entries."""
        return self._native.size

    def __len__(self) -> int:
        return len(self._native)

    async def get(self, key: str) -> CacheEntry | None:  # noqa: D102
        found = self._native.get(key)
        return None if found is None else CacheEntry(*found)

    async def set(  # noqa: D102
        self, key: str, value: bytes, ttl: float, *, stored_at: float | None = None
    ) -> None:
        self._native.set(key, value, ttl, time.time() if stored_at is None else stored_at)

    def put(self, key: str, value: bytes, ttl: float, stored_at: float) -> None:
        """Store `value` with a given store time (synchronous)."""
        self._native.set(key, value, ttl, stored_at)

    def fill(self, key: str, value: bytes, ttl: float, stored_at: float) -> bool:
        """Store a copy of an entry unless a newer one is held; True when stored."""
        return self._native.fill(key, value, ttl, stored_at)

    async def close(self) -> None:  # noqa: D102
        self._native.clear()


# One failed command is not an outage: a slow TLS connect to a distant Redis
# or a lost packet would otherwise bypass Redis for the whole cooldown. The
# breaker opens after this many failures in a row within the window; any
# command that reaches Redis resets the count. Each failing command still
# waits only for its own timeout.
FAILURES_TO_TRIP = 3
FAILURE_WINDOW = 10.0


class RedisCache:
    """Redis-backed cache that degrades to "always miss" while Redis is down."""

    backend = "redis"

    def __init__(
        self,
        client: Redis,
        cooldown: float = 30.0,
        *,
        failures_to_trip: int = FAILURES_TO_TRIP,
        failure_window: float = FAILURE_WINDOW,
    ) -> None:
        self.client = client
        self._cooldown = cooldown
        self._failures_to_trip = max(failures_to_trip, 1)
        self._failure_window = failure_window
        self._failures: deque[float] = deque()
        self._disabled_until = 0.0
        self._pool_warned_at = -cooldown

    @property
    def available(self) -> bool:
        """False while the circuit breaker is open after a Redis failure."""
        return time.monotonic() >= self._disabled_until

    def trip(self, exc: Exception) -> None:
        """Count a failed Redis command; open the circuit breaker after several.

        An exhausted connection pool counts for nothing: the command never
        reached Redis, and skipping Redis would send every worker to t.me instead.
        """
        now = time.monotonic()
        if isinstance(exc, MaxConnectionsError):
            if now - self._pool_warned_at >= self._cooldown:
                self._pool_warned_at = now
                logger.warning("Redis connection pool exhausted, skipping a command: %s", exc)
            return
        while self._failures and now - self._failures[0] > self._failure_window:
            self._failures.popleft()
        self._failures.append(now)
        if len(self._failures) < self._failures_to_trip:
            logger.info(
                "Redis command failed (%d of %d before bypassing it): %s",
                len(self._failures),
                self._failures_to_trip,
                exc,
            )
            return
        self._failures.clear()
        if self.available:
            logger.warning("Redis unavailable, bypassing it for %.0fs: %s", self._cooldown, exc)
        self._disabled_until = now + self._cooldown

    def succeeded(self) -> None:
        """Reset the failure count: a command reached Redis."""
        if self._failures:
            self._failures.clear()

    async def warm_up(self) -> bool:
        """Open a connection now, so the first request does not pay for it."""
        try:
            await self.client.ping()
        except (RedisError, OSError) as exc:
            self.trip(exc)
            return False
        self.succeeded()
        return True

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
        self.succeeded()
        return decode_entry(raw)

    async def get_with_ttl(self, key: str) -> tuple[CacheEntry, float | None] | None:
        """The entry for `key` and its remaining TTL in seconds (None if unknown).

        Both come from one round trip (a pipeline of GET and PTTL).
        """
        if not self.available:
            return None
        try:
            async with self.client.pipeline(transaction=False) as pipe:
                pipe.execute_command("GET", KEY_PREFIX + key, **{NEVER_DECODE: True})
                pipe.pttl(KEY_PREFIX + key)
                raw, pttl = await pipe.execute()
        except (RedisError, OSError) as exc:
            self.trip(exc)
            return None
        self.succeeded()
        entry = decode_entry(raw)
        if entry is None:
            return None
        # PTTL is -1 for a key without expiry and -2 for one gone meanwhile.
        return entry, pttl / 1000 if isinstance(pttl, int) and pttl > 0 else None

    async def set(  # noqa: D102
        self, key: str, value: bytes, ttl: float, *, stored_at: float | None = None
    ) -> None:
        if ttl <= 0 or not self.available:
            return
        stored_at = time.time() if stored_at is None else stored_at
        try:
            await self.client.set(
                KEY_PREFIX + key, b"%.3f\n%b" % (stored_at, value), px=int(ttl * 1000)
            )
        except (RedisError, OSError) as exc:
            self.trip(exc)
        else:
            self.succeeded()

    async def close(self) -> None:  # noqa: D102
        await self.client.aclose()


def decode_entry(raw: object) -> CacheEntry | None:
    """Parse a stored value: the Unix time it was stored at, a newline, the payload."""
    if not isinstance(raw, bytes):
        return None
    stored_at, separator, value = raw.partition(b"\n")
    try:
        return CacheEntry(value, float(stored_at)) if separator else None
    except ValueError:
        return None


class TwoLevelCache:
    """The in-process cache in front of the shared Redis cache.

    Reads try the in-process level first; a miss reads Redis and keeps a copy
    for the entry's remaining TTL, at most `max_local_ttl`. Writes go to both.
    While Redis is unavailable (or its circuit breaker is open) the in-process
    level works alone, and keeps what is written for the full TTL.

    `max_local_ttl` bounds how long a server keeps serving its own copy after
    another server (or the feed worker) has written a newer one to Redis:
    without it, a latest page kept for the feed's stale window would be
    re-fetched from t.me instead of picked up from Redis once it is older than
    the page TTL.
    """

    backend = "memory+redis"

    def __init__(self, local: MemoryCache, shared: RedisCache, max_local_ttl: float) -> None:
        self.local = local
        self.shared = shared
        self._max_local_ttl = max_local_ttl
        # Concurrent misses of one key share a single Redis read.
        self._reads: SingleFlight[CacheEntry | None] = SingleFlight()

    async def get(self, key: str) -> CacheEntry | None:  # noqa: D102
        entry = await self.local.get(key)
        if entry is not None or not self.shared.available:
            return entry
        return await self._reads.run(key, lambda: self._read_shared(key))

    async def _read_shared(self, key: str) -> CacheEntry | None:
        found = await self.shared.get_with_ttl(key)
        if found is None:
            return None
        entry, ttl = found
        local_ttl = self._max_local_ttl if ttl is None else min(ttl, self._max_local_ttl)
        # A write that landed meanwhile is newer than this copy and stays.
        self.local.fill(key, entry.value, local_ttl, entry.stored_at)
        return entry

    async def set(  # noqa: D102
        self, key: str, value: bytes, ttl: float, *, stored_at: float | None = None
    ) -> None:
        stored_at = time.time() if stored_at is None else stored_at
        local_ttl = min(ttl, self._max_local_ttl) if self.shared.available else ttl
        self.local.put(key, value, local_ttl, stored_at)
        await self.shared.set(key, value, ttl, stored_at=stored_at)

    async def close(self) -> None:  # noqa: D102
        await self.local.close()
        await self.shared.close()


class WaitingConnectionPool(BlockingConnectionPool):
    """Connection pool where a burst waits up to `timeout` for a free connection.

    redis-py's default pool fails a command at once when every connection is
    in use. When the wait runs out this one raises `MaxConnectionsError`, which
    `RedisCache.trip` tells apart from a Redis failure.
    """

    async def get_connection(self, *args: object, **kwargs: object) -> Connection:  # noqa: D102
        try:
            return await super().get_connection(*args, **kwargs)  # type: ignore[no-untyped-call,no-any-return]
        except RedisConnectionError as exc:
            # The wait timed out; connect failures raise other errors or causes.
            if type(exc) is RedisConnectionError and isinstance(exc.__cause__, TimeoutError):
                raise MaxConnectionsError(str(exc)) from exc
            raise


def redis_max_connections(settings: Settings) -> int:
    """Redis connections per worker (see `_MIN_REDIS_CONNECTIONS`)."""
    return max(_MIN_REDIS_CONNECTIONS, 2 * settings.telegram_max_concurrency)


def create_redis(settings: Settings) -> Redis | None:
    """Build a Redis client with short timeouts, or None when REDIS_URL is unset."""
    if not settings.redis_url:
        return None
    pool = WaitingConnectionPool.from_url(
        settings.redis_url,
        max_connections=redis_max_connections(settings),
        timeout=settings.redis_timeout,
        decode_responses=True,
        socket_timeout=settings.redis_timeout,
        # Opening a connection to a distant TLS endpoint takes several round
        # trips (TCP, TLS, then AUTH/HELLO under socket_timeout): ~0.3-0.7 s
        # at 90 ms. Under REDIS_TIMEOUT every reconnect after an error would
        # time out and reopen the breaker, so Redis would never come back.
        socket_connect_timeout=settings.redis_connect_timeout,
        health_check_interval=30,
    )
    return Redis.from_pool(pool)


def create_cache(settings: Settings, redis: Redis | None) -> Cache:
    """The in-process cache, in front of Redis when there is one."""
    local = MemoryCache(settings.memory_cache_max_mb * 1024 * 1024)
    if redis is None:
        return local
    return TwoLevelCache(local, RedisCache(redis), settings.memory_cache_max_ttl)


def shared_cache(cache: Cache) -> RedisCache | None:
    """The Redis level of `cache`, if it has one."""
    if isinstance(cache, TwoLevelCache):
        return cache.shared
    return cache if isinstance(cache, RedisCache) else None
