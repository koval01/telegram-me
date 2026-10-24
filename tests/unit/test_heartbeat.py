"""Heartbeat to Redis: node markers, probing while the breaker is open."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import override

import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from telegram_me.cache import FAILURES_TO_TRIP, KEY_PREFIX, NODE_PREFIX, RedisCache
from telegram_me.heartbeat import beating, node_id


@pytest.fixture
async def redis_cache() -> AsyncIterator[RedisCache]:
    cache = RedisCache(FakeAsyncRedis(decode_responses=True))
    yield cache
    await cache.close()


def _available(cache: RedisCache) -> bool:
    # A call, not the property: mypy would keep `available` narrowed to False
    # across the awaits that change it, and call the rest unreachable.
    return cache.available


class _Flaky(FakeAsyncRedis):
    """Fails while `down` is set."""

    down = False

    @override
    async def set(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        if self.down:
            raise RedisConnectionError("down")
        return await super().set(*args, **kwargs)  # type: ignore[arg-type]


async def test_heartbeat_writes_a_short_lived_marker(redis_cache: RedisCache) -> None:
    assert await redis_cache.heartbeat("node:a", "x", ttl=15)
    assert await redis_cache.client.get(KEY_PREFIX + "node:a") == "x"
    assert 14_000 < await redis_cache.client.pttl(KEY_PREFIX + "node:a") <= 15_000


async def test_failed_heartbeats_count_towards_the_breaker() -> None:
    client = _Flaky(decode_responses=True)
    client.down = True
    cache = RedisCache(client)
    for _ in range(FAILURES_TO_TRIP - 1):
        assert not await cache.heartbeat("node:a", "x", ttl=15)
    assert cache.available
    assert not await cache.heartbeat("node:a", "x", ttl=15)
    assert not cache.available


async def test_heartbeat_probes_and_closes_an_open_breaker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _Flaky(decode_responses=True)
    cache = RedisCache(client, cooldown=30, failures_to_trip=1)
    client.down = True
    await cache.heartbeat("node:a", "x", ttl=15)
    assert not _available(cache)
    client.down = False
    # Sent despite the open breaker; the answer closes it without the cooldown.
    assert await cache.heartbeat("node:a", "x", ttl=15)
    assert cache.available
    assert "Redis answers again" in caplog.text


async def test_a_heartbeat_resets_the_failure_count(redis_cache: RedisCache) -> None:
    for _ in range(3):
        for _ in range(FAILURES_TO_TRIP - 1):
            redis_cache.trip(RedisConnectionError("slow"))
        assert await redis_cache.heartbeat("node:a", "x", ttl=15)
    assert redis_cache.available


async def test_beating_writes_this_process_marker_and_removes_it(
    redis_cache: RedisCache,
) -> None:
    key = KEY_PREFIX + NODE_PREFIX + node_id()
    async with beating(redis_cache, interval=0.02):
        await asyncio.sleep(0.07)
        marker = json.loads(await redis_cache.client.get(key) or "{}")
        assert set(marker) == {"version", "started", "at"}
        first = marker["at"]
        await asyncio.sleep(0.05)
        assert json.loads(await redis_cache.client.get(key) or "{}")["at"] > first
        assert 0 < await redis_cache.client.pttl(key) <= 60
    assert await redis_cache.client.get(key) is None


async def test_beating_disabled_with_zero_interval(redis_cache: RedisCache) -> None:
    async with beating(redis_cache, interval=0):
        await asyncio.sleep(0.02)
    assert await redis_cache.client.keys(KEY_PREFIX + NODE_PREFIX + "*") == []


async def test_beating_survives_an_outage() -> None:
    client = _Flaky(decode_responses=True)
    cache = RedisCache(client, failures_to_trip=1)
    key = KEY_PREFIX + NODE_PREFIX + node_id()
    client.down = True
    async with beating(cache, interval=0.02):
        await asyncio.sleep(0.05)
        assert not _available(cache)
        client.down = False
        await asyncio.sleep(0.05)
        assert cache.available
        assert await client.get(key) is not None
