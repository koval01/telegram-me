"""Aggregated, ranked feed over several channels.

Pages are served with stale-while-revalidate semantics: a page younger than
FEED_FRESH_TTL is used as is, an older one (up to FEED_STALE_TTL) is used
immediately while a background refresh replaces it, and only missing pages
are fetched inline, all of them in parallel. The refresher keeps popular
channels warm so most feed requests never wait for t.me.

Python decides which pages to use; everything after that runs in one call
to the native extension (`telegram_me_native`, Rust) on a worker thread with
the GIL released: reading the cached pages, the ranking signals, per-channel
engagement normalisation, freshness, collapsing of near-duplicate posts
across channels, topic detection, diversity re-ranking and rendering the
response. Pages travel as the bytes the cache holds and come back as the
response bytes, so a feed request builds no Python objects per post.

Identical concurrent feed requests (REST or GraphQL) are built once. Channel
lists are compared case-insensitively, keeping their order (it decides ties).
"""

import asyncio
import contextlib
import itertools
import logging
import math
import time
from typing import Any

import telegram_me_native as native
from pydantic_core import from_json

from telegram_me.services import upstream
from telegram_me.services.channels import ChannelService, LoadedPage
from telegram_me.services.coalesce import SingleFlight
from telegram_me.services.popularity import LeaderLock, Popularity
from telegram_me.settings import Settings

logger = logging.getLogger(__name__)


class FeedService:
    """Builds the ranked feed."""

    def __init__(
        self, channels: ChannelService, popularity: Popularity, settings: Settings
    ) -> None:
        self._channels = channels
        self._popularity = popularity
        self._settings = settings
        self._feeds: SingleFlight[bytes] = SingleFlight()
        self._entries: SingleFlight[list[dict[str, Any]]] = SingleFlight()
        self._flows = itertools.count()

    def rank_options(self, now: float | None = None) -> native.RankOptions:
        """Ranking parameters for the current moment."""
        settings = self._settings
        return native.RankOptions(
            now=int(time.time() if now is None else now),
            half_life_hours=settings.feed_half_life_hours,
            diversity=settings.feed_diversity,
            collapse_duplicates=settings.feed_collapse_duplicates,
        )

    async def build_json(self, channels: list[str]) -> bytes:
        """Ranked posts of all channels as the `/v1/feed` response body."""
        key, unique = _normalized(channels)
        return await self._feeds.run(key, lambda: self._build_json(unique))

    async def _build_json(self, unique: list[str]) -> bytes:
        hit = asyncio.ensure_future(self._popularity.hit(unique))
        # One flow upstream: a feed of many cold channels must not crowd out
        # other requests (see `services.upstream`).
        with upstream.flow(f"feed:{next(self._flows)}"):
            pages = await asyncio.gather(
                *(self._page(channel) for channel in unique), return_exceptions=True
            )
        await hit

        found: list[tuple[str, bytes]] = []
        for channel, page in zip(unique, pages, strict=True):
            if isinstance(page, BaseException):
                logger.warning("Feed skipped channel %s: %r", channel, page)
            else:
                found.append((channel, page))
        feed, unreadable = await asyncio.to_thread(native.build_feed, found, self.rank_options())
        for index in unreadable:
            logger.warning("Feed skipped channel %s: corrupted page in cache", found[index][0])
        return feed

    async def build(self, channels: list[str]) -> list[dict[str, Any]]:
        """`build_json()` decoded: the feed entries (`result`) as Python objects.

        Decoded once for all concurrent identical calls; the shared result is
        read-only.
        """
        key, _unique = _normalized(channels)
        return await self._entries.run(key, lambda: self._decoded(channels))

    async def _decoded(self, channels: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = from_json(await self.build_json(channels))["result"]
        return result

    async def _page(self, channel: str) -> bytes:
        """The channel's latest page as the page cache stores it."""
        entry = await self._channels.cached_page(channel)
        if entry is not None:
            if entry.age < self._settings.feed_fresh_ttl:
                return entry.value
            if entry.age < self._settings.feed_stale_ttl:
                self._channels.refresh_page_in_background(channel)
                return entry.value
        return (await self._channels.refresh_page(channel)).raw


def _normalized(channels: list[str]) -> tuple[str, list[str]]:
    """Coalescing key and the channels without case-insensitive repeats (first spelling wins)."""
    unique: dict[str, str] = {}
    for channel in channels:
        unique.setdefault(channel.lower(), channel)
    return ",".join(unique), list(unique.values())


class FeedRefresher:
    """Background loop that keeps popular channels' pages fresh.

    The more popular a channel, the more often it is refreshed: the interval is
    FEED_REFRESH_MAX_INTERVAL / (1 + score), clamped to the configured bounds.
    At most `concurrency` channels are refreshed at a time, so a tick neither
    takes every upstream slot from user requests nor lands hundreds of pages
    on the parser (and their cache writes on Redis) at once. Refreshes run at
    background priority upstream, and a page that any process fetched within
    the last half interval is left alone.
    """

    tick_seconds = 5.0
    decay_every_seconds = 60.0
    concurrency = 16

    def __init__(
        self,
        channels: ChannelService,
        popularity: Popularity,
        lock: LeaderLock,
        settings: Settings,
    ) -> None:
        self._channels = channels
        self._popularity = popularity
        self._lock = lock
        self._settings = settings
        self._last_refresh: dict[str, float] = {}
        self._last_decay = time.monotonic()

    def interval(self, score: float) -> float:
        """Refresh interval for a channel with the given popularity score."""
        settings = self._settings
        interval = settings.feed_refresh_max_interval / (1.0 + max(score, 0.0))
        return min(
            max(interval, settings.feed_refresh_min_interval), settings.feed_refresh_max_interval
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Refresh until `stop` is set."""
        logger.info("Feed refresher started")
        upstream.run_in_background()
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    logger.exception("Feed refresher tick failed")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.tick_seconds)
        finally:
            await self._lock.release()
            logger.info("Feed refresher stopped")

    async def tick(self) -> int:
        """Run one scheduling round; returns the number of refreshed channels."""
        if not await self._lock.acquire():
            return 0

        now = time.monotonic()
        if (elapsed := now - self._last_decay) >= self.decay_every_seconds:
            half_life = self._settings.feed_popularity_half_life
            await self._popularity.decay(math.pow(0.5, elapsed / half_life))
            self._last_decay = now

        due = [
            (channel, self.interval(score))
            for channel, score in await self._popularity.top(self._settings.feed_refresh_top)
            if now - self._last_refresh.get(channel, -math.inf) >= self.interval(score)
        ]
        if not due:
            return 0

        slots = asyncio.Semaphore(self.concurrency)

        async def refresh(channel: str, interval: float) -> LoadedPage:
            async with slots:
                return await self._channels.refresh_page(channel, fresh_for=interval / 2)

        results = await asyncio.gather(
            *(refresh(channel, interval) for channel, interval in due), return_exceptions=True
        )
        refreshed = 0
        for (channel, _interval), result in zip(due, results, strict=True):
            self._last_refresh[channel] = now
            if isinstance(result, BaseException):
                logger.info("Refresh of %s failed: %r", channel, result)
            else:
                refreshed += 1
        logger.debug("Refreshed %d/%d popular channels", refreshed, len(due))
        return refreshed
