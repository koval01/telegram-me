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
    FAILURE_WINDOW,
    FAILURES_TO_TRIP,
    KEY_PREFIX,
    CacheEntry,
    MemoryCache,
    RedisCache,
    TwoLevelCache,
    WaitingConnectionPool,
    create_cache,
    create_redis,
    shared_cache,
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


async def test_memory_cache_expires() -> None:
    # Expiry runs on the native cache's own monotonic clock.
    cache = MemoryCache(max_bytes=4096)
    await cache.set("k", b"v", ttl=0.05)
    await cache.set("long", b"v", ttl=60)
    assert await cache.get("k") is not None
    await asyncio.sleep(0.08)
    assert await cache.get("k") is None
    assert await cache.get("long") is not None


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


async def test_redis_cache_get_with_ttl(redis_cache: RedisCache) -> None:
    await redis_cache.set("k", b"v", ttl=60, stored_at=123.5)
    found = await redis_cache.get_with_ttl("k")
    assert found is not None
    entry, ttl = found
    assert entry == CacheEntry(b"v", 123.5)
    assert ttl is not None
    assert 59 < ttl <= 60
    assert await redis_cache.get_with_ttl("missing") is None
    # A key written without expiry has no known TTL.
    await redis_cache.client.set(KEY_PREFIX + "forever", "1.0\nv")
    assert await redis_cache.get_with_ttl("forever") == (CacheEntry(b"v", 1.0), None)


async def test_redis_cache_ignores_foreign_values(redis_cache: RedisCache) -> None:
    await redis_cache.client.set(KEY_PREFIX + "k", "legacy value without header")
    assert await redis_cache.get("k") is None


class _BrokenRedis(FakeAsyncRedis):
    calls = 0

    async def execute_command(self, *_args: object, **_kwargs: object) -> None:
        type(self).calls += 1
        raise RedisConnectionError("down")

    def pipeline(self, *_args: object, **_kwargs: object) -> Any:
        return _BrokenPipeline()


class _BrokenPipeline:
    async def __aenter__(self) -> "_BrokenPipeline":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __getattr__(self, _name: str) -> Any:
        return lambda *_args, **_kwargs: self

    async def execute(self) -> None:
        _BrokenRedis.calls += 1
        raise RedisConnectionError("down")


async def test_redis_outage_opens_circuit_breaker(caplog: pytest.LogCaptureFixture) -> None:
    _BrokenRedis.calls = 0
    cache = RedisCache(_BrokenRedis(decode_responses=True), cooldown=30)
    for _ in range(FAILURES_TO_TRIP - 1):
        assert await cache.get("k") is None
        assert cache.available  # one failure is not an outage
    assert "Redis unavailable" not in caplog.text
    assert await cache.get("k") is None
    assert not cache.available
    assert await cache.get("k") is None  # skipped without touching Redis
    await cache.set("k", b"v", ttl=60)  # no-op while open
    assert _BrokenRedis.calls == FAILURES_TO_TRIP
    assert "Redis unavailable" in caplog.text


async def test_a_success_resets_the_failure_count(redis_cache: RedisCache) -> None:
    for _ in range(5):
        for _ in range(FAILURES_TO_TRIP - 1):
            redis_cache.trip(RedisConnectionError("slow connect"))
        assert await redis_cache.get("k") is None  # reaches Redis: a miss, not a failure
    assert redis_cache.available


def test_failures_outside_the_window_do_not_add_up(
    redis_cache: RedisCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr("telegram_me.cache.time.monotonic", lambda: now[0])
    for _ in range(5):
        for _ in range(FAILURES_TO_TRIP - 1):
            redis_cache.trip(RedisConnectionError("slow connect"))
        now[0] += FAILURE_WINDOW + 1
    assert redis_cache.available
    for _ in range(FAILURES_TO_TRIP):
        redis_cache.trip(RedisConnectionError("down"))
    assert not redis_cache.available


def test_failure_threshold_is_configurable() -> None:
    cache = RedisCache(FakeAsyncRedis(), failures_to_trip=1)
    cache.trip(RedisConnectionError("down"))
    assert not cache.available


async def test_warm_up_opens_a_connection(redis_cache: RedisCache) -> None:
    assert await redis_cache.warm_up()
    assert redis_cache.available


async def test_failed_warm_up_counts_as_one_failure() -> None:
    cache = RedisCache(_BrokenRedis(decode_responses=True), cooldown=30)
    assert not await cache.warm_up()
    assert cache.available
    assert not await RedisCache(_BrokenRedis(), failures_to_trip=1).warm_up()


async def test_redis_get_with_ttl_trips_circuit_breaker() -> None:
    cache = RedisCache(_BrokenRedis(decode_responses=True), cooldown=30)
    for _ in range(FAILURES_TO_TRIP):
        assert await cache.get_with_ttl("k") is None
    assert not cache.available
    assert await cache.get_with_ttl("k") is None


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
    # Commands keep the short timeout; opening a connection (TLS included) gets longer.
    assert pool.connection_kwargs["socket_timeout"] == 0.25
    assert pool.connection_kwargs["socket_connect_timeout"] == settings.redis_connect_timeout == 2.0
    await client.aclose()

    small = create_redis(make_settings(redis_url="redis://localhost", telegram_max_concurrency=4))
    assert small is not None
    assert small.connection_pool.max_connections == 32
    await small.aclose()


def test_create_cache_picks_backend() -> None:
    memory = create_cache(make_settings(memory_cache_max_mb=3), None)
    assert isinstance(memory, MemoryCache)
    assert memory.backend == "memory"
    assert shared_cache(memory) is None

    two_level = create_cache(make_settings(), FakeAsyncRedis())
    assert isinstance(two_level, TwoLevelCache)
    assert two_level.backend == "memory+redis"
    assert shared_cache(two_level) is two_level.shared
    assert shared_cache(two_level.shared) is two_level.shared


async def test_memory_cache_is_safe_under_concurrency() -> None:
    cache = MemoryCache(max_bytes=10 * await _entry_size("k10", b"x" * 10))
    await asyncio.gather(*(cache.set(f"k{i}", b"x" * 10, ttl=60) for i in range(50)))
    hits = [await cache.get(f"k{i}") for i in range(50)]
    assert sum(hit is not None for hit in hits) == 10


# Two levels ------------------------------------------------------------------------------


@pytest.fixture
async def two_level() -> AsyncIterator[TwoLevelCache]:
    cache = TwoLevelCache(
        MemoryCache(2**20), RedisCache(FakeAsyncRedis(decode_responses=True)), max_local_ttl=60
    )
    yield cache
    await cache.close()


class _CountingReads:
    """Wraps `RedisCache.get_with_ttl` to count calls, optionally slowed down."""

    def __init__(self, cache: RedisCache, delay: float = 0.0) -> None:
        self.calls = 0
        self._read = cache.get_with_ttl
        self._delay = delay
        cache.get_with_ttl = self  # type: ignore[method-assign]

    async def __call__(self, key: str) -> tuple[CacheEntry, float | None] | None:
        self.calls += 1
        found = await self._read(key)
        await asyncio.sleep(self._delay)  # the reply is on its way back
        return found


async def test_two_level_writes_both_levels_with_one_store_time(two_level: TwoLevelCache) -> None:
    await two_level.set("k", b"value", ttl=60)
    local, shared = await two_level.local.get("k"), await two_level.shared.get("k")
    assert local is not None
    assert shared is not None
    assert local.value == shared.value == b"value"
    assert shared.stored_at == pytest.approx(local.stored_at, abs=0.001)  # Redis keeps ms
    assert 0 < await two_level.shared.client.pttl(KEY_PREFIX + "k") <= 60_000


async def test_two_level_hit_skips_redis(two_level: TwoLevelCache) -> None:
    await two_level.set("k", b"value", ttl=60)
    reads = _CountingReads(two_level.shared)
    entry = await two_level.get("k")
    assert entry is not None
    assert entry.value == b"value"
    assert reads.calls == 0


async def test_two_level_miss_reads_redis_and_fills_local(two_level: TwoLevelCache) -> None:
    await two_level.shared.set("k", b"shared", ttl=60, stored_at=123.0)
    reads = _CountingReads(two_level.shared)
    assert await two_level.get("k") == CacheEntry(b"shared", 123.0)
    # The copy keeps the original store time: ages (and the feed's
    # stale-while-revalidate) are the same on every server.
    assert await two_level.local.get("k") == CacheEntry(b"shared", 123.0)
    assert await two_level.get("k") == CacheEntry(b"shared", 123.0)
    assert reads.calls == 1
    assert await two_level.get("missing") is None
    assert await two_level.local.get("missing") is None


async def test_two_level_copy_expires_with_the_redis_entry(two_level: TwoLevelCache) -> None:
    await two_level.shared.set("k", b"v", ttl=0.05)
    assert await two_level.get("k") is not None
    assert await two_level.local.get("k") is not None
    await asyncio.sleep(0.08)
    assert await two_level.local.get("k") is None
    assert await two_level.get("k") is None


async def test_two_level_local_ttl_is_capped() -> None:
    cache = TwoLevelCache(
        MemoryCache(2**20), RedisCache(FakeAsyncRedis(decode_responses=True)), max_local_ttl=0.05
    )
    await cache.set("written", b"v", ttl=60)
    await cache.shared.set("read", b"v", ttl=60)
    await cache.shared.client.set(KEY_PREFIX + "no-ttl", "1.0\nv")
    for key in ("read", "no-ttl"):
        assert await cache.get(key) is not None
    await asyncio.sleep(0.08)
    for key in ("written", "read", "no-ttl"):
        assert await cache.local.get(key) is None
        assert await cache.get(key) is not None  # read again from Redis
    await cache.close()


async def test_two_level_sees_newer_writes_of_other_servers_after_the_cap() -> None:
    redis = FakeAsyncRedis(decode_responses=True)
    first, second = (
        TwoLevelCache(MemoryCache(2**20), RedisCache(redis), max_local_ttl=0.05) for _ in range(2)
    )
    await first.set("page", b"old", ttl=600)
    assert (await second.get("page") or CacheEntry(b"", 0)).value == b"old"
    await first.set("page", b"new", ttl=600)
    assert (await second.get("page") or CacheEntry(b"", 0)).value == b"old"  # its own copy
    await asyncio.sleep(0.08)
    assert (await second.get("page") or CacheEntry(b"", 0)).value == b"new"


async def test_two_level_concurrent_misses_share_one_redis_read(two_level: TwoLevelCache) -> None:
    await two_level.shared.set("k", b"v", ttl=60)
    reads = _CountingReads(two_level.shared, delay=0.01)
    entries = await asyncio.gather(*(two_level.get("k") for _ in range(20)))
    assert {entry.value if entry else None for entry in entries} == {b"v"}
    assert reads.calls == 1


async def test_two_level_read_does_not_replace_a_newer_local_write(
    two_level: TwoLevelCache,
) -> None:
    await two_level.shared.set("k", b"old", ttl=60, stored_at=1.0)
    _CountingReads(two_level.shared, delay=0.02)
    read = asyncio.create_task(two_level.get("k"))
    await asyncio.sleep(0.005)
    await two_level.set("k", b"new", ttl=60)  # lands while the read is in flight
    old = await read
    assert old is not None
    assert old.value == b"old"
    current = await two_level.get("k")
    assert current is not None
    assert current.value == b"new"


async def test_two_level_runs_on_local_cache_while_redis_is_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _BrokenRedis.calls = 0
    shared = RedisCache(_BrokenRedis(decode_responses=True), failures_to_trip=1)
    cache = TwoLevelCache(MemoryCache(2**20), shared, max_local_ttl=0.05)
    await cache.set("k", b"v", ttl=60)  # opens the circuit breaker
    assert _BrokenRedis.calls == 1
    assert not cache.shared.available
    assert "Redis unavailable" in caplog.text
    # While Redis is skipped, the in-process level keeps entries for their
    # full TTL: there is no shared copy to pick up instead.
    await cache.set("later", b"v", ttl=60)
    await asyncio.sleep(0.08)
    entry = await cache.get("later")
    assert entry is not None
    assert entry.value == b"v"
    assert await cache.get("missing") is None
    assert _BrokenRedis.calls == 1  # neither read nor write touched Redis


async def test_two_level_open_breaker_skips_redis_reads(two_level: TwoLevelCache) -> None:
    await two_level.shared.set("k", b"v", ttl=60)
    reads = _CountingReads(two_level.shared)
    for _ in range(FAILURES_TO_TRIP):
        two_level.shared.trip(RedisConnectionError("down"))
    assert await two_level.get("k") is None
    assert reads.calls == 0
