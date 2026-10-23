import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import time_machine
from fakeredis import FakeAsyncRedis, FakeServer
from fakeredis.aioredis import FakeAsyncRedisConnection
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from telegram_me.cache import (
    KEY_PREFIX,
    MemoryCache,
    RedisCache,
    WaitingConnectionPool,
    create_cache,
    create_redis,
)
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


class _SlowConnection(FakeAsyncRedisConnection):
    """Holds its pooled connection across an await, like a network round trip."""

    async def read_response(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.001)
        return await super().read_response(*args, **kwargs)


def _small_pool_redis(pool_class: type[ConnectionPool], size: int, **kwargs: Any) -> Redis:
    pool = pool_class(
        connection_class=_SlowConnection,
        server=FakeServer(),
        max_connections=size,
        decode_responses=True,
        **kwargs,
    )
    return Redis.from_pool(pool)


async def _burst(cache: RedisCache, count: int = 50) -> None:
    await asyncio.gather(*(cache.set(f"k{i}", b"v%d" % i, ttl=60) for i in range(count)))


async def test_exhausted_pool_does_not_open_circuit_breaker() -> None:
    # redis-py's default pool fails a command at once when every connection is
    # busy: a burst like a 50-channel feed exhausts a pool of two.
    cache = RedisCache(_small_pool_redis(ConnectionPool, 2))
    await _burst(cache)
    stored = await cache.client.keys(KEY_PREFIX + "*")
    assert 0 < len(stored) < 50
    assert cache.available
    await cache.close()


async def test_burst_waits_for_a_pooled_connection() -> None:
    cache = RedisCache(_small_pool_redis(WaitingConnectionPool, 2, timeout=5))
    await _burst(cache)
    entries = await asyncio.gather(*(cache.get(f"k{i}") for i in range(50)))
    assert [entry.value if entry else None for entry in entries] == [b"v%d" % i for i in range(50)]
    assert cache.available
    await cache.close()


async def test_pool_wait_timeout_skips_command_without_tripping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = RedisCache(_small_pool_redis(WaitingConnectionPool, 1, timeout=0.01))
    await cache.set("k", b"v", ttl=60)
    pool = cache.client.connection_pool
    assert isinstance(pool, WaitingConnectionPool)
    held = await pool.get_connection()
    assert await cache.get("k") is None
    await cache.set("other", b"v", ttl=60)
    assert cache.available
    assert caplog.text.count("Redis connection pool exhausted") == 1
    assert "Redis unavailable" not in caplog.text

    await pool.release(held)
    entry = await cache.get("k")
    assert entry is not None
    assert entry.value == b"v"
    await cache.close()


async def test_create_redis_pool_is_sized_from_upstream_concurrency() -> None:
    assert create_redis(make_settings()) is None
    settings = make_settings(redis_url="redis://localhost:6379/0", redis_timeout=0.25)
    client = create_redis(settings)
    assert client is not None
    pool = client.connection_pool
    assert isinstance(pool, WaitingConnectionPool)
    assert pool.max_connections == 2 * settings.telegram_max_concurrency
    assert pool.timeout == 0.25
    await client.aclose()

    small = create_redis(make_settings(redis_url="redis://localhost", telegram_max_concurrency=4))
    assert small is not None
    assert small.connection_pool.max_connections == 32
    await small.aclose()


def test_create_cache_picks_backend() -> None:
    assert create_cache(make_settings(), None).backend == "memory"
    assert create_cache(make_settings(), FakeAsyncRedis()).backend == "redis"


async def test_memory_cache_is_safe_under_concurrency() -> None:
    cache = MemoryCache(max_bytes=10 * await _entry_size("k10", b"x" * 10))
    await asyncio.gather(*(cache.set(f"k{i}", b"x" * 10, ttl=60) for i in range(50)))
    hits = [await cache.get(f"k{i}") for i in range(50)]
    assert sum(hit is not None for hit in hits) == 10
