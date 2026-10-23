"""Construction and lifetime of the application's long-lived objects."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass

from telegram_me.cache import Cache, RedisCache, create_cache, create_redis
from telegram_me.services.channels import ChannelService
from telegram_me.services.feed import FeedRefresher, FeedService
from telegram_me.services.popularity import (
    LeaderLock,
    LocalLeaderLock,
    MemoryPopularity,
    Popularity,
    RedisLeaderLock,
    RedisPopularity,
)
from telegram_me.settings import Settings
from telegram_me.telegram.client import Fetcher, ReqwestFetcher, TelegramClient

logger = logging.getLogger(__name__)

_LEADER_TTL = 30.0


@dataclass(frozen=True, slots=True)
class Container:
    """Everything request handlers and the refresher need."""

    settings: Settings
    cache: Cache
    client: TelegramClient
    channels: ChannelService
    feed: FeedService
    popularity: Popularity
    leader_lock: LeaderLock

    def refresher(self) -> FeedRefresher:
        """A refresher wired to this container's services."""
        return FeedRefresher(self.channels, self.popularity, self.leader_lock, self.settings)


@asynccontextmanager
async def build_container(
    settings: Settings, fetcher: Fetcher | None = None
) -> AsyncIterator[Container]:
    """Create the container and close every resource on exit."""
    async with AsyncExitStack() as stack:
        cache = create_cache(settings, create_redis(settings))
        stack.push_async_callback(cache.close)

        fetcher = fetcher or ReqwestFetcher(timeout=settings.telegram_timeout)
        client = TelegramClient(fetcher, max_concurrency=settings.telegram_max_concurrency)
        stack.push_async_callback(client.close)

        popularity: Popularity
        leader_lock: LeaderLock
        if isinstance(cache, RedisCache):
            popularity = RedisPopularity(cache)
            leader_lock = RedisLeaderLock(cache, ttl=_LEADER_TTL)
        else:
            popularity = MemoryPopularity()
            leader_lock = LocalLeaderLock()

        channels = ChannelService(client, cache, settings)
        yield Container(
            settings=settings,
            cache=cache,
            client=client,
            channels=channels,
            feed=FeedService(channels, popularity, settings),
            popularity=popularity,
            leader_lock=leader_lock,
        )


@asynccontextmanager
async def running_refresher(container: Container) -> AsyncIterator[None]:
    """Run the feed refresher in the background for the duration of the block."""
    stop = asyncio.Event()
    task = asyncio.create_task(container.refresher().run(stop), name="feed-refresher")
    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
