from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from telegram_me.cache import RedisCache
from telegram_me.services.popularity import (
    LEADER_KEY,
    LocalLeaderLock,
    MemoryPopularity,
    RedisLeaderLock,
    RedisPopularity,
)


@pytest.fixture
async def redis_cache() -> AsyncIterator[RedisCache]:
    cache = RedisCache(FakeAsyncRedis(decode_responses=True))
    yield cache
    await cache.close()


async def test_memory_popularity_ranks_and_decays() -> None:
    popularity = MemoryPopularity()
    await popularity.hit(["durov", "Telegram"])
    await popularity.hit(["durov"])
    assert await popularity.top(10) == [("durov", 2.0), ("telegram", 1.0)]

    await popularity.decay(0.5)
    assert await popularity.top(1) == [("durov", 1.0)]

    for _ in range(10):
        await popularity.decay(0.5)
    assert await popularity.top(10) == []


async def test_redis_popularity_ranks_and_decays(redis_cache: RedisCache) -> None:
    popularity = RedisPopularity(redis_cache)
    await popularity.hit(["durov", "telegram", "durov"])
    assert await popularity.top(10) == [("durov", 2.0), ("telegram", 1.0)]

    await popularity.decay(0.5)
    assert await popularity.top(10) == [("durov", 1.0), ("telegram", 0.5)]

    await popularity.decay(0.04)  # durov 0.04, telegram 0.02: both below 0.05
    assert await popularity.top(10) == []


async def test_local_lock_is_always_leader() -> None:
    lock = LocalLeaderLock()
    assert await lock.acquire()
    await lock.release()


async def test_redis_lock_elects_single_leader(redis_cache: RedisCache) -> None:
    first = RedisLeaderLock(redis_cache, ttl=30)
    second = RedisLeaderLock(redis_cache, ttl=30)

    assert await first.acquire()
    assert not await second.acquire()
    assert await first.acquire()  # renewal keeps leadership

    await first.release()
    assert await second.acquire()


async def test_redis_lock_detects_lost_leadership(redis_cache: RedisCache) -> None:
    first = RedisLeaderLock(redis_cache, ttl=30)
    second = RedisLeaderLock(redis_cache, ttl=30)
    assert await first.acquire()

    await redis_cache.client.delete(LEADER_KEY)  # e.g. expired while paused
    assert await second.acquire()
    assert not await first.acquire()
