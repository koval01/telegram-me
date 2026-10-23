"""Channel popularity for the feed refresher, plus single-leader election.

Popularity is the number of feed requests that included a channel, with
exponential decay: a request counts half as much after one half-life. With
Redis the ranking and the leader lock are shared by every instance; without
it each process keeps its own.
"""

import logging
import secrets
import time
from collections.abc import Iterable
from typing import Protocol, cast

from redis.exceptions import MaxConnectionsError, RedisError

from telegram_me.cache import KEY_PREFIX, RedisCache

logger = logging.getLogger(__name__)

POPULARITY_KEY = f"{KEY_PREFIX}feed:popularity"
LEADER_KEY = f"{KEY_PREFIX}feed:refresher-leader"
# Channels whose decayed score falls below this are forgotten.
_FORGET_BELOW = 0.05

# Renew the lock only if we still own it.
_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
# Delete the lock only if we still own it.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class Popularity(Protocol):
    """Decayed request counters per channel."""

    async def hit(self, channels: Iterable[str]) -> None:
        """Count one feed request for each channel."""
        ...

    async def top(self, limit: int) -> list[tuple[str, float]]:
        """Most popular channels with their scores, best first."""
        ...

    async def decay(self, factor: float) -> None:
        """Multiply every score by `factor` and forget cold channels."""
        ...


class LeaderLock(Protocol):
    """Ensures only one refresher runs across all instances."""

    async def acquire(self) -> bool:
        """Take or renew leadership; True while this process is the leader."""
        ...

    async def release(self) -> None:
        """Give leadership up (best effort)."""
        ...


class MemoryPopularity:
    """Process-local popularity counters."""

    def __init__(self) -> None:
        self._scores: dict[str, float] = {}

    async def hit(self, channels: Iterable[str]) -> None:  # noqa: D102
        for channel in channels:
            key = channel.lower()
            self._scores[key] = self._scores.get(key, 0.0) + 1.0

    async def top(self, limit: int) -> list[tuple[str, float]]:  # noqa: D102
        return sorted(self._scores.items(), key=lambda item: item[1], reverse=True)[:limit]

    async def decay(self, factor: float) -> None:  # noqa: D102
        self._scores = {
            channel: score * factor
            for channel, score in self._scores.items()
            if score * factor >= _FORGET_BELOW
        }


class RedisPopularity:
    """Popularity stored in a Redis sorted set shared by all instances."""

    def __init__(self, cache: RedisCache) -> None:
        self._cache = cache

    async def hit(self, channels: Iterable[str]) -> None:  # noqa: D102
        if not self._cache.available:
            return
        try:
            async with self._cache.client.pipeline(transaction=False) as pipe:
                for channel in channels:
                    pipe.zincrby(POPULARITY_KEY, 1, channel.lower())
                await pipe.execute()
        except (RedisError, OSError) as exc:
            self._cache.trip(exc)

    async def top(self, limit: int) -> list[tuple[str, float]]:  # noqa: D102
        if not self._cache.available:
            return []
        try:
            items = await self._cache.client.zrevrange(
                POPULARITY_KEY, 0, limit - 1, withscores=True
            )
        except (RedisError, OSError) as exc:
            self._cache.trip(exc)
            return []
        pairs = cast("list[tuple[str, float]]", items)
        return [(str(channel), float(score)) for channel, score in pairs]

    async def decay(self, factor: float) -> None:  # noqa: D102
        if not self._cache.available:
            return
        try:
            async with self._cache.client.pipeline(transaction=True) as pipe:
                pipe.zunionstore(POPULARITY_KEY, {POPULARITY_KEY: factor})
                pipe.zremrangebyscore(POPULARITY_KEY, "-inf", f"({_FORGET_BELOW}")
                await pipe.execute()
        except (RedisError, OSError) as exc:
            self._cache.trip(exc)


class LocalLeaderLock:
    """Without shared state every process is its own leader."""

    async def acquire(self) -> bool:  # noqa: D102
        return True

    async def release(self) -> None:  # noqa: D102
        return None


class RedisLeaderLock:
    """Leader election with an expiring Redis key owned by a random token."""

    def __init__(self, cache: RedisCache, ttl: float) -> None:
        self._cache = cache
        self._ttl_ms = int(ttl * 1000)
        self._token = secrets.token_hex(16)
        self._leader_since: float | None = None

    async def acquire(self) -> bool:  # noqa: D102
        if not self._cache.available:
            return False
        client = self._cache.client
        try:
            if self._leader_since is not None:
                renewed = await client.eval(_RENEW_SCRIPT, 1, LEADER_KEY, self._token, self._ttl_ms)
                if renewed:
                    return True
                logger.info("Lost feed refresher leadership")
                self._leader_since = None
            if await client.set(LEADER_KEY, self._token, nx=True, px=self._ttl_ms):
                self._leader_since = time.monotonic()
                logger.info("Became feed refresher leader")
                return True
        except (RedisError, OSError) as exc:
            self._cache.trip(exc)
            if not isinstance(exc, MaxConnectionsError):  # else nothing reached Redis
                self._leader_since = None
        return False

    async def release(self) -> None:  # noqa: D102
        if self._leader_since is None or not self._cache.available:
            return
        try:
            await self._cache.client.eval(_RELEASE_SCRIPT, 1, LEADER_KEY, self._token)
        except (RedisError, OSError) as exc:
            self._cache.trip(exc)
        self._leader_since = None
