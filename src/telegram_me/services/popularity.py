"""Channel popularity for the feed refresher, plus single-leader election.

Popularity is the number of feed requests that included a channel, with
exponential decay: a request counts half as much after one half-life. With
Redis the ranking and the leader lock are shared by every instance; without
it each process keeps its own, unless the peer layer shares them: every node
then counts every node's requests, and the live node with the lowest address
leads.
"""

import asyncio
import logging
import math
import secrets
import time
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from typing import Protocol, cast

from redis.exceptions import MaxConnectionsError, RedisError

from telegram_me.cache import KEY_PREFIX, RedisCache
from telegram_me.peers import Peers

logger = logging.getLogger(__name__)

POPULARITY_KEY = f"{KEY_PREFIX}feed:popularity"
LEADER_KEY = f"{KEY_PREFIX}feed:refresher-leader"
# Channels whose decayed score falls below this are forgotten.
_FORGET_BELOW = 0.05
# Peer popularity: hits are sent this often, at most this many channels each.
_HITS_TOPIC = "hits"
_HITS_INTERVAL = 1.0
_HITS_MAX_CHANNELS = 5000
_DECAY_INTERVAL = 60.0

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


class PeerLeaderLock:
    """Leadership by the peer layer: the live node with the lowest address leads.

    Every node decides alone from its view of the members. Views that differ
    for a moment (a node joining or leaving) at worst make two nodes refresh
    the same pages, which costs a few duplicate fetches and nothing else.
    """

    def __init__(self, peers: Peers) -> None:
        self._peers = peers
        self._leading = False

    async def acquire(self) -> bool:  # noqa: D102
        leading = self._peers.is_leader()
        if leading != self._leading:
            logger.info(
                "Became feed refresher leader" if leading else "Lost feed refresher leadership"
            )
        self._leading = leading
        return leading

    async def release(self) -> None:  # noqa: D102
        self._leading = False


class PeerPopularity(MemoryPopularity):
    """Popularity every node counts for all: hits are sent to the other nodes.

    Each node adds the hits it sees and the ones the others send (once a
    second, see `sharing`), so whichever node leads knows every node's
    requests. Each node also decays its own copy on the same schedule, and
    `decay()` (the leader's call) does nothing: copies that decay together
    stay alike, and one that just became leader is not a stale one.
    """

    def __init__(self, peers: Peers, half_life: float) -> None:
        super().__init__()
        self._peers = peers
        self._half_life = half_life
        self._pending: Counter[str] = Counter()
        peers.on_cast(_HITS_TOPIC, self._received)

    async def hit(self, channels: Iterable[str]) -> None:  # noqa: D102
        channels = [channel.lower() for channel in channels]
        await super().hit(channels)
        self._pending.update(channels)

    async def decay(self, factor: float) -> None:  # noqa: D102
        return None

    def _received(self, data: bytes) -> None:
        for line in data.decode("utf-8", "replace").splitlines():
            channel, _, count = line.partition("\t")
            try:
                hits = float(count)
            except ValueError:
                continue
            if channel and math.isfinite(hits) and hits > 0:
                self._scores[channel] = self._scores.get(channel, 0.0) + hits

    def flush(self) -> None:
        """Send the hits counted since the last call to the other nodes."""
        if not self._pending:
            return
        hits = self._pending.most_common(_HITS_MAX_CHANNELS)
        self._pending.clear()
        self._peers.cast(_HITS_TOPIC, "\n".join(f"{c}\t{n}" for c, n in hits).encode())

    @asynccontextmanager
    async def sharing(self) -> AsyncIterator[None]:
        """Send hits and decay the scores while the block runs."""

        async def loop() -> None:
            decayed_at = time.monotonic()
            while True:
                await asyncio.sleep(_HITS_INTERVAL)
                self.flush()
                if (elapsed := time.monotonic() - decayed_at) >= _DECAY_INTERVAL:
                    await MemoryPopularity.decay(self, math.pow(0.5, elapsed / self._half_life))
                    decayed_at = time.monotonic()

        task = asyncio.create_task(loop(), name="peer-popularity")
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
