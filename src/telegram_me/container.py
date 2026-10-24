"""Construction and lifetime of the application's long-lived objects."""

import asyncio
import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass

from telegram_me.cache import (
    Cache,
    PeerCache,
    RedisCache,
    TwoLevelCache,
    create_cache,
    create_redis,
    shared_cache,
)
from telegram_me.heartbeat import beating
from telegram_me.memory import trimming_memory
from telegram_me.peers import Peers
from telegram_me.services.channels import ChannelService
from telegram_me.services.feed import FeedRefresher, FeedService
from telegram_me.services.lease import Lease, LocalLease, PeerLease, RedisLease
from telegram_me.services.popularity import (
    LeaderLock,
    LocalLeaderLock,
    MemoryPopularity,
    PeerLeaderLock,
    PeerPopularity,
    Popularity,
    RedisLeaderLock,
    RedisPopularity,
)
from telegram_me.services.upstream import FairGate
from telegram_me.settings import Settings
from telegram_me.telegram.client import Fetcher, ReqwestFetcher, TelegramClient

logger = logging.getLogger(__name__)

_LEADER_TTL = 30.0
# A fetch lease lives this long unless its holder extends it (see RedisLease).
_LEASE_TTL = 3.0
# Threads that parse and render fetched pages off the event loop. The native
# parser releases the GIL, so two keep up with a busy worker; more would only
# contend with the event loop for the GIL and each add a malloc arena.
_PARSE_THREADS = 2


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
    lease: Lease
    peers: Peers | None = None

    def refresher(self) -> FeedRefresher:
        """A refresher wired to this container's services."""
        return FeedRefresher(self.channels, self.popularity, self.leader_lock, self.settings)


@asynccontextmanager
async def build_container(
    settings: Settings, fetcher: Fetcher | None = None, *, refresher: bool | None = None
) -> AsyncIterator[Container]:
    """Create the container and close every resource on exit.

    `refresher` tells whether this process may run the feed refresher (by
    default: when it is embedded in the API); with peers, one that may leads.
    """
    if refresher is None:
        refresher = settings.enable_feed and settings.feed_refresh_mode == "embedded"
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(trimming_memory(settings.memory_trim_interval))
        inner = create_cache(settings, create_redis(settings))
        stack.push_async_callback(inner.close)
        local = inner.local if isinstance(inner, TwoLevelCache) else inner
        shared = shared_cache(inner)
        peers = None
        if settings.enable_peers:
            peers = Peers.start(settings, local, refresher=refresher)
        cache: Cache = inner if peers is None else PeerCache(inner, peers)
        if shared is not None:
            await shared.warm_up()
            await stack.enter_async_context(
                beating(
                    shared,
                    settings.redis_heartbeat_interval,
                    peer=None if peers is None else peers.addr,
                )
            )
        if peers is not None:
            # Leaves the cluster before the cache and Redis close.
            await stack.enter_async_context(peers.running(shared))

        fetcher = fetcher or ReqwestFetcher(
            timeout=settings.telegram_timeout, base_url=settings.telegram_base_url
        )
        gate = FairGate(settings.telegram_max_concurrency, settings.upstream_queue_limit)
        client = TelegramClient(fetcher, settings.telegram_max_concurrency, gate)
        stack.push_async_callback(client.close)

        popularity = _popularity(settings, shared, peers)
        if isinstance(popularity, PeerPopularity):
            await stack.enter_async_context(popularity.sharing())
        leader_lock: LeaderLock
        if peers is not None:
            leader_lock = PeerLeaderLock(peers)
        elif shared is not None:
            leader_lock = RedisLeaderLock(shared, ttl=_LEADER_TTL)
        else:
            leader_lock = LocalLeaderLock()

        # A live holder (or owner) may retry once after a network error.
        max_wait = 2 * settings.telegram_timeout + _LEASE_TTL
        lease: Lease = LocalLease()
        if shared is not None and settings.shared_coalescing:
            lease = RedisLease(
                shared,
                ttl=_LEASE_TTL,
                max_wait=max_wait,
                local=local,
                max_local_ttl=settings.memory_cache_max_ttl,
            )
            # Runs before the cache closes: pending lease releases still reach Redis.
            stack.push_async_callback(lease.drain)
        if peers is not None:
            lease = PeerLease(peers, lease, max_wait)

        executor = stack.enter_context(
            ThreadPoolExecutor(_PARSE_THREADS, thread_name_prefix="telegram-me-parse")
        )
        channels = ChannelService(client, cache, settings, executor, lease)
        if peers is not None:
            peers.serve_with(channels.serve_peer)
        yield Container(
            settings=settings,
            cache=cache,
            client=client,
            channels=channels,
            feed=FeedService(channels, popularity, settings),
            popularity=popularity,
            leader_lock=leader_lock,
            lease=lease,
            peers=peers,
        )


def _popularity(settings: Settings, shared: RedisCache | None, peers: Peers | None) -> Popularity:
    """Shared in Redis, else among the peers, else kept by this process alone."""
    if shared is not None:
        return RedisPopularity(shared)
    if peers is not None:
        return PeerPopularity(peers, settings.feed_popularity_half_life)
    return MemoryPopularity()


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
