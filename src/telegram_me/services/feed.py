"""Aggregated, ranked feed over several channels.

Pages are served with stale-while-revalidate semantics: a page younger than
FEED_FRESH_TTL is used as is, an older one (up to FEED_STALE_TTL) is used
immediately while a background refresh replaces it, and only missing pages
are fetched inline, all of them in parallel. The refresher keeps popular
channels warm so most feed requests never wait for t.me.
"""

import asyncio
import contextlib
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any

from telegram_me.services.channels import ChannelService, decode_page
from telegram_me.services.popularity import LeaderLock, Popularity
from telegram_me.services.scoring import (
    ScoredPost,
    content_summary,
    parse_compact_number,
    parse_published_at,
    reaction_counts,
)
from telegram_me.settings import Settings
from telegram_me.telegram.parsing.html import JSONDict

logger = logging.getLogger(__name__)


class FeedService:
    """Builds the ranked feed."""

    def __init__(
        self, channels: ChannelService, popularity: Popularity, settings: Settings
    ) -> None:
        self._channels = channels
        self._popularity = popularity
        self._settings = settings
        self._simplified = settings.parser_mode == "simplified"

    async def build(self, channels: list[str]) -> list[dict[str, Any]]:
        """Posts of all channels, best score first, without duplicates."""
        unique = list(dict.fromkeys(channels))
        hit = asyncio.ensure_future(self._popularity.hit(unique))
        pages = await asyncio.gather(
            *(self._page(channel) for channel in unique), return_exceptions=True
        )
        await hit

        now = datetime.now(UTC)
        posts: list[dict[str, Any]] = []
        for channel, page in zip(unique, pages, strict=True):
            if isinstance(page, BaseException):
                logger.warning("Feed skipped channel %s: %r", channel, page)
                continue
            posts.extend(self._scored_posts(channel, page, now))

        posts.sort(key=lambda post: post.get("_score", 0), reverse=True)
        seen: set[tuple[str, int]] = set()
        result = []
        for post in posts:
            key = (post["channel"]["username"], post["id"])
            if key not in seen:
                seen.add(key)
                result.append(post)
        return result

    async def _page(self, channel: str) -> JSONDict:
        entry = await self._channels.cached_page(channel)
        if entry is not None:
            if entry.age < self._settings.feed_fresh_ttl:
                return decode_page(entry.value)
            if entry.age < self._settings.feed_stale_ttl:
                self._channels.refresh_page_in_background(channel)
                return decode_page(entry.value)
        return await self._channels.refresh_page(channel)

    def _scored_posts(self, channel: str, page: JSONDict, now: datetime) -> list[dict[str, Any]]:
        posts_data = page.get("content", {}).get("posts", [])
        info = page.get("channel", {})
        subscribers = parse_compact_number(info.get("counters", {}).get("subscribers", "1"), 1)

        scored = []
        for post in posts_data:
            footer = post.get("footer", {})
            content = post.get("content", {})
            score = ScoredPost(
                published_at=parse_published_at(footer.get("date", {}).get("string", "")),
                views=parse_compact_number(footer.get("views", "0"), 0),
                content=content_summary(content),
                edited=footer.get("edited", False),
                reactions={} if self._simplified else reaction_counts(content.get("reacts")),
                subscribers=subscribers,
            ).score(now)
            scored.append({**post, "channel": info, "_score": score})
        logger.debug("Scored %d posts of %s", len(scored), channel)
        return scored


class FeedRefresher:
    """Background loop that keeps popular channels' pages fresh.

    The more popular a channel, the more often it is refreshed: the interval is
    FEED_REFRESH_MAX_INTERVAL / (1 + score), clamped to the configured bounds.
    """

    tick_seconds = 5.0
    decay_every_seconds = 60.0

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

        results = await asyncio.gather(
            *(self._channels.refresh_page(channel) for channel in due), return_exceptions=True
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
