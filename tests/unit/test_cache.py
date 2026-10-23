import asyncio
import os
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
        await cache.set("k", b"value", ttl=60)
        clock.shift(5)
        entry = await cache.get("k")
    assert entry is not None
    assert entry.value == b"value"
    assert entry.stored_at == 1_000_000


async def test_memory_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = MemoryCache(max_bytes=1024)
    now = [100.0]
    monkeypatch.setattr("telegram_me.cache.time.monotonic", lambda: now[0])
    await cache.set("k", b"v", ttl=10)
    now[0] = 109.0
    assert await cache.get("k") is not None
    now[0] = 110.0
    assert await cache.get("k") is None


async def _entry_size(key: str, value: bytes) -> int:
    probe = MemoryCache(max_bytes=2**20)
    await probe.set(key, value, ttl=60)
    return probe.size


async def test_memory_cache_evicts_least_recently_used_by_size() -> None:
    entry = await _entry_size("a", b"aaaa")
    cache = MemoryCache(max_bytes=2 * entry + entry // 2)
    await cache.set("a", b"aaaa", ttl=60)
    await cache.set("b", b"bbbb", ttl=60)
    assert await cache.get("a") is not None  # "a" becomes most recently used
    await cache.set("c", b"cccc", ttl=60)
    assert await cache.get("b") is None
    assert await cache.get("a") is not None
    assert await cache.get("c") is not None
    assert len(cache) == 2


async def test_memory_cache_overwrite_keeps_size_accurate() -> None:
    entry = await _entry_size("a", b"aaaa")
    cache = MemoryCache(max_bytes=2 * entry)
    for _ in range(10):
        await cache.set("a", b"aaaa", ttl=60)
    assert cache.size == entry
    await cache.set("b", b"bbbb", ttl=60)
    assert await cache.get("a") is not None
    assert await cache.get("b") is not None


async def test_memory_cache_counts_real_memory_and_compresses() -> None:
    cache = MemoryCache(max_bytes=2**20)
    # Compressible JSON with emoji: stored smaller than its UTF-8 form, and
    # returned byte for byte.
    page = ('{"text":"Привет 👋 world","views":"1.2K"},' * 500).encode()
    await cache.set("page", page, ttl=60)
    entry = await cache.get("page")
    assert entry is not None
    assert entry.value == page
    assert cache.size < len(page) // 4
    # Incompressible data: at least its own size is accounted.
    noise = os.urandom(10_000)
    await cache.set("noise", noise, ttl=60)
    assert cache.size > len(noise)
    await cache.close()
    assert cache.size == 0


async def test_memory_cache_ignores_non_positive_ttl_and_huge_values() -> None:
    cache = MemoryCache(max_bytes=4)
    await cache.set("a", b"v", ttl=0)
    await cache.set("b", b"too large", ttl=60)
    assert await cache.get("a") is None
    assert await cache.get("b") is None


async def test_redis_cache_roundtrip(redis_cache: RedisCache) -> None:
    await redis_cache.set("k", b"line1\nline2", ttl=60)
    entry = await redis_cache.get("k")
    assert entry is not None
    assert entry.value == b"line1\nline2"
    assert entry.age >= 0
    assert 0 < await redis_cache.client.pttl(KEY_PREFIX + "k") <= 60_000


async def test_redis_cache_ignores_foreign_values(redis_cache: RedisCache) -> None:
    await redis_cache.client.set(KEY_PREFIX + "k", "legacy value without header")
    assert await redis_cache.get("k") is None


class _BrokenRedis(FakeAsyncRedis):
    calls = 0

    async def execute_command(self, *_args: object, **_kwargs: object) -> None:
        type(self).calls += 1
        raise RedisConnectionError("down")


async def test_redis_outage_opens_circuit_breaker(caplog: pytest.LogCaptureFixture) -> None:
    cache = RedisCache(_BrokenRedis(decode_responses=True), cooldown=30)
    assert await cache.get("k") is None
    assert not cache.available
    assert await cache.get("k") is None  # skipped without touching Redis
    await cache.set("k", b"v", ttl=60)  # no-op while open
    assert _BrokenRedis.calls == 1
    assert "Redis unavailable" in caplog.text


def test_create_cache_picks_backend() -> None:
    assert create_cache(make_settings(), None).backend == "memory"
    assert create_cache(make_settings(), FakeAsyncRedis()).backend == "redis"


async def test_memory_cache_is_safe_under_concurrency() -> None:
    cache = MemoryCache(max_bytes=10 * await _entry_size("k10", b"x" * 10))
    await asyncio.gather(*(cache.set(f"k{i}", b"x" * 10, ttl=60) for i in range(50)))
    hits = [await cache.get(f"k{i}") for i in range(50)]
    assert sum(hit is not None for hit in hits) == 10
