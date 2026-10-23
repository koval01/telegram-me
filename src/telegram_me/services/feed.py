"""Aggregated, ranked feed over several channels.

Pages are served with stale-while-revalidate semantics: a page younger than
FEED_FRESH_TTL is used as is, an older one (up to FEED_STALE_TTL) is used
immediately while a background refresh replaces it, and only missing pages
are fetched inline, all of them in parallel. The refresher keeps popular
channels warm so most feed requests never wait for t.me.

Ranking runs in the native extension (`telegram_me_native`, Rust) on a worker
thread with the GIL released: per-channel engagement normalisation,
freshness, collapsing of near-duplicate posts across channels, topic
detection and diversity re-ranking.
"""

import asyncio
import contextlib
import logging
import math
import time
from typing import Any

import telegram_me_native as native

from telegram_me.services.channels import ChannelService, decode_page
from telegram_me.services.feed_signals import candidate, parse_compact_number
from telegram_me.services.popularity import LeaderLock, Popularity
from telegram_me.settings import Settings
from telegram_me.telegram.parsing.pages import JSONDict

logger = logging.getLogger(__name__)


class FeedService:
    """Builds the ranked feed."""

    def __init__(
        self, channels: ChannelService, popularity: Popularity, settings: Settings
    ) -> None:
        self._channels = channels
        self._popularity = popularity
        self._settings = settings

    def rank_options(self, now: float | None = None) -> native.RankOptions:
        """Ranking parameters for the current moment."""
        settings = self._settings
        return native.RankOptions(
            now=int(time.time() if now is None else now),
            half_life_hours=settings.feed_half_life_hours,
            diversity=settings.feed_diversity,
            collapse_duplicates=settings.feed_collapse_duplicates,
        )

    async def build(self, channels: list[str]) -> list[dict[str, Any]]:
        """Ranked posts of all channels (see the module docstring)."""
        unique = list(dict.fromkeys(channels))
        hit = asyncio.ensure_future(self._popularity.hit(unique))
        pages = await asyncio.gather(
            *(self._page(channel) for channel in unique), return_exceptions=True
        )
        await hit

        posts: list[dict[str, Any]] = []
        candidates: list[native.Candidate] = []
        seen: set[tuple[str, int]] = set()
        for channel, page in zip(unique, pages, strict=True):
            if isinstance(page, BaseException):
                logger.warning("Feed skipped channel %s: %r", channel, page)
                continue
            info = page.get("channel", {})
            username = str(info.get("username") or channel)
            subscribers = parse_compact_number((info.get("counters") or {}).get("subscribers"), 1)
            for post in page.get("content", {}).get("posts", []):
                key = (username, int(post["id"]))
                if key in seen:
                    continue
                seen.add(key)
                posts.append({**post, "channel": info})
                candidates.append(candidate(username, subscribers, post))

        items, topics = await asyncio.to_thread(native.rank, candidates, self.rank_options())
        return [self._entry(posts, item, topics) for item in items]

    @staticmethod
    def _entry(
        posts: list[dict[str, Any]], item: native.RankedItem, topics: list[native.Topic]
    ) -> dict[str, Any]:
        entry = {**posts[item.index], "_score": item.score}
        if item.duplicates:
            entry["_duplicates"] = [
                {"channel": posts[i]["channel"].get("username"), "id": posts[i]["id"]}
                for i in item.duplicates
            ]
        if item.topic is not None and len(topics[item.topic].members) > 1:
            entry["_topic"] = {"id": item.topic, "keywords": topics[item.topic].keywords}
        return entry

    async def _page(self, channel: str) -> JSONDict:
        entry = await self._channels.cached_page(channel)
        if entry is not None:
            if entry.age < self._settings.feed_fresh_ttl:
                return decode_page(entry.value)
            if entry.age < self._settings.feed_stale_ttl:
                self._channels.refresh_page_in_background(channel)
                return decode_page(entry.value)
        return await self._channels.refresh_page(channel)


class FeedRefresher:
    """Background loop that keeps popular channels' pages fresh.

    The more popular a channel, the more often it is refreshed: the interval is
    FEED_REFRESH_MAX_INTERVAL / (1 + score), clamped to the configured bounds.
    At most `concurrency` channels are refreshed at a time, so a tick neither
    takes every upstream slot from user requests nor lands hundreds of pages
    on the parser (and their cache writes on Redis) at once.
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
            channel
            for channel, score in await self._popularity.top(self._settings.feed_refresh_top)
            if now - self._last_refresh.get(channel, -math.inf) >= self.interval(score)
        ]
        if not due:
            return 0

        slots = asyncio.Semaphore(self.concurrency)

        async def refresh(channel: str) -> JSONDict:
            async with slots:
                return await self._channels.refresh_page(channel)

        results = await asyncio.gather(
            *(refresh(channel) for channel in due), return_exceptions=True
        )
        refreshed = 0
        for channel, result in zip(due, results, strict=True):
            self._last_refresh[channel] = now
            if isinstance(result, BaseException):
                logger.info("Refresh of %s failed: %r", channel, result)
            else:
                refreshed += 1
        logger.debug("Refreshed %d/%d popular channels", refreshed, len(due))
        return refreshed
