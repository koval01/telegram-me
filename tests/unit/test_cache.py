import asyncio
from collections.abc import AsyncIterator

import pytest
import time_machine
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from telegram_me.cache import KEY_PREFIX, MemoryCache, RedisCache, create_cache
from tests.conftest import make_settings


@pytest.fixture
async def redis_cache() -> AsyncIterator[RedisCache]:
    cache = RedisCache(FakeAsyncRedis(decode_responses=True))
    yield cache
    await cache.close()


async def test_memory_cache_roundtrip_and_age() -> None:
    cache = MemoryCache(max_bytes=1024)
    with time_machine.travel(1_000_000, tick=False) as clock:
        await cache.set("k", "value", ttl=60)
        clock.shift(5)
        entry = await cache.get("k")
    assert entry is not None
    assert entry.value == "value"
    assert entry.stored_at == 1_000_000


async def test_memory_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = MemoryCache(max_bytes=1024)
    now = [100.0]
    monkeypatch.setattr("telegram_me.cache.time.monotonic", lambda: now[0])
    await cache.set("k", "v", ttl=10)
    now[0] = 109.0
    assert await cache.get("k") is not None
    now[0] = 110.0
    assert await cache.get("k") is None


async def test_memory_cache_evicts_least_recently_used_by_size() -> None:
    cache = MemoryCache(max_bytes=10)
    await cache.set("a", "aaaa", ttl=60)
    await cache.set("b", "bbbb", ttl=60)
    assert await cache.get("a") is not None  # "a" becomes most recently used
    await cache.set("c", "cccc", ttl=60)
    assert await cache.get("b") is None
    assert await cache.get("a") is not None
    assert await cache.get("c") is not None


async def test_memory_cache_overwrite_keeps_size_accurate() -> None:
    cache = MemoryCache(max_bytes=8)
    for _ in range(10):
        await cache.set("a", "aaaa", ttl=60)
    await cache.set("b", "bbbb", ttl=60)
    assert await cache.get("a") is not None
    assert await cache.get("b") is not None


async def test_memory_cache_ignores_non_positive_ttl_and_huge_values() -> None:
    cache = MemoryCache(max_bytes=4)
    await cache.set("a", "v", ttl=0)
    await cache.set("b", "too large", ttl=60)
    assert await cache.get("a") is None
    assert await cache.get("b") is None


async def test_redis_cache_roundtrip(redis_cache: RedisCache) -> None:
    await redis_cache.set("k", "line1\nline2", ttl=60)
    entry = await redis_cache.get("k")
    assert entry is not None
    assert entry.value == "line1\nline2"
    assert entry.age >= 0
    assert 0 < await redis_cache.client.pttl(KEY_PREFIX + "k") <= 60_000


async def test_redis_cache_ignores_foreign_values(redis_cache: RedisCache) -> None:
    await redis_cache.client.set(KEY_PREFIX + "k", "legacy value without header")
    assert await redis_cache.get("k") is None


class _BrokenRedis(FakeAsyncRedis):
    calls = 0

    async def get(self, *_args: object, **_kwargs: object) -> None:
        type(self).calls += 1
        raise RedisConnectionError("down")


async def test_redis_outage_opens_circuit_breaker(caplog: pytest.LogCaptureFixture) -> None:
    cache = RedisCache(_BrokenRedis(decode_responses=True), cooldown=30)
    assert await cache.get("k") is None
    assert not cache.available
    assert await cache.get("k") is None  # skipped without touching Redis
    await cache.set("k", "v", ttl=60)  # no-op while open
    assert _BrokenRedis.calls == 1
    assert "Redis unavailable" in caplog.text


def test_create_cache_picks_backend() -> None:
    assert create_cache(make_settings(), None).backend == "memory"
    assert create_cache(make_settings(), FakeAsyncRedis()).backend == "redis"


async def test_memory_cache_is_safe_under_concurrency() -> None:
    cache = MemoryCache(max_bytes=100)
    await asyncio.gather(*(cache.set(f"k{i}", "x" * 10, ttl=60) for i in range(50)))
    hits = [await cache.get(f"k{i}") for i in range(50)]
    assert sum(hit is not None for hit in hits) == 10
