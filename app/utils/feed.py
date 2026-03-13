"""Telegram feed aggregation service."""

import asyncio
import logging
import re
from typing import List, Dict, Any, Tuple

import httpx
from httpx import AsyncClient

from app.telegram.telegram import Telegram
from app.utils.feed_parts.http import get_global_client
from app.utils.features import cache_mode, parser_mode
from app.utils.feed_parts.parsers import (
    parse_content_type_single,
    parse_datetime_fast,
    parse_numeric_value,
    parse_reactions_single,
)
from app.utils.feed_parts.scoring import ScoredPost

logger = logging.getLogger(__name__)


class PostDataPreparer:
    """Prepare aggregated feed data across multiple Telegram channels."""

    def __init__(self) -> None:
        self.telegram = Telegram()
        self._comments_pattern = re.compile(r"js-header[^>]*>(\d+)\s*comment", re.IGNORECASE)
        self._channel_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._comment_cache: dict[str, tuple[int, float]] = {}
        selected_cache_mode = cache_mode()
        if selected_cache_mode == "off":
            self._cache_ttl = 0
        elif selected_cache_mode == "aggressive":
            self._cache_ttl = 300
        else:
            self._cache_ttl = 60
        self._simplified_parser = parser_mode() == "simplified"

    async def fetch_channel_data_batch(self, usernames: List[str]) -> Dict[str, Any]:
        """Fetch channel bodies in parallel with short timeout and local TTL cache."""
        tasks = {}
        for username in usernames:
            cache_key = username.lower()
            if cache_key in self._channel_cache:
                _, timestamp = self._channel_cache[cache_key]
                if asyncio.get_event_loop().time() - timestamp < self._cache_ttl:
                    continue

            tasks[username] = asyncio.wait_for(self.telegram.body(username), timeout=8.0)

        if tasks:
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            current_time = asyncio.get_event_loop().time()
            for username, result in zip(tasks.keys(), results):
                if not isinstance(result, Exception) and result:
                    self._channel_cache[username.lower()] = (result, current_time)

        channel_data = {}
        for username in usernames:
            cache_key = username.lower()
            if cache_key in self._channel_cache:
                channel_data[username] = self._channel_cache[cache_key][0]

        return channel_data

    # pylint: disable=too-many-locals
    async def fetch_comments_count_ultra_batch(
        self,
        post_infos: List[Tuple[str, int]],
        client: AsyncClient,
    ) -> Dict[Tuple[str, int], int]:
        """Fetch comments count for post list using shared HTTP/2 client."""
        if not post_infos:
            return {}

        current_time = asyncio.get_event_loop().time()
        results: dict[tuple[str, int], int] = {}
        remaining_posts = []

        for post_info in post_infos:
            cache_key = f"{post_info[0]}/{post_info[1]}"
            if cache_key in self._comment_cache:
                data, timestamp = self._comment_cache[cache_key]
                if current_time - timestamp < self._cache_ttl:
                    results[post_info] = data
                    continue
            remaining_posts.append(post_info)

        if not remaining_posts:
            return results

        batch_size = 20
        all_batch_results: dict[tuple[str, int], int] = {}
        for i in range(0, len(remaining_posts), batch_size):
            batch = remaining_posts[i : i + batch_size]
            batch_results = await self._fetch_comment_batch_with_fallback(batch, client)
            all_batch_results.update(batch_results)
            if i + batch_size < len(remaining_posts):
                await asyncio.sleep(0.05)

        for post_info, count in all_batch_results.items():
            cache_key = f"{post_info[0]}/{post_info[1]}"
            self._comment_cache[cache_key] = (count, current_time)
            results[post_info] = count

        return results

    async def _fetch_comment_batch_with_fallback(
        self,
        post_infos: List[Tuple[str, int]],
        client: AsyncClient,
    ) -> Dict[Tuple[str, int], int]:
        """Fetch comments in parallel; fallback to zeros on batch failure."""
        try:
            tasks = []
            for username, post_id in post_infos:
                url = f"https://t.me/{username}/{post_id}?embed=1&discussion=1&comments_limit=1"
                tasks.append(asyncio.wait_for(self._fetch_single_comment_fast(url, client), timeout=6.0))

            counts = await asyncio.gather(*tasks, return_exceptions=True)
            return {
                post_info: 0 if isinstance(count, Exception) else count
                for post_info, count in zip(post_infos, counts)
            }
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Batch comment fetch failed, using zeros: %s", exc)
            return {post_info: 0 for post_info in post_infos}

    async def _fetch_single_comment_fast(self, url: str, client: AsyncClient) -> int:
        """Fetch a single comments count value from embed page."""
        try:
            response = await client.get(url)
            match = self._comments_pattern.search(response.text)
            return int(match.group(1)) if match else 0
        except (asyncio.TimeoutError, httpx.HTTPError):
            return 0

    async def prepare_multiple_channels(
        self,
        usernames: List[str],
        max_concurrent: int = 8,
    ) -> List[Dict[str, Any]]:
        """Build merged, scored post list across channels."""
        if not usernames:
            return []

        client = get_global_client()
        channel_data_map = await self.fetch_channel_data_batch(usernames)

        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = []
        for username in usernames:
            if username in channel_data_map:
                tasks.append(
                    self._process_channel_posts(
                        username,
                        channel_data_map[username],
                        client,
                        semaphore,
                    )
                )

        channel_results = await asyncio.gather(*tasks, return_exceptions=True)
        all_posts = []
        for result in channel_results:
            if isinstance(result, Exception):
                logger.error("Channel processing failed: %s", result)
            elif result:
                all_posts.extend(result)
        return all_posts

    # pylint: disable=too-many-locals
    async def _process_channel_posts(
        self,
        username: str,
        channel_data: Dict[str, Any],
        client: AsyncClient,
        semaphore: asyncio.Semaphore,
    ) -> List[Dict[str, Any]]:
        """Process all posts for one channel."""
        async with semaphore:
            try:
                posts_data = channel_data.get("content", {}).get("posts", [])
                if not posts_data:
                    return []

                channel_info = channel_data.get("channel", {})
                subscribers = parse_numeric_value(channel_info.get("counters", {}).get("subscribers", "1"), 1)
                channel_title = channel_info.get("title", {}).get("string", username)

                post_infos = [(username, post.get("id", 0)) for post in posts_data]
                comments_map = (
                    {}
                    if self._simplified_parser
                    else await self.fetch_comments_count_ultra_batch(post_infos, client)
                )

                final_posts = []
                for post_data in posts_data:
                    scored_post = self._build_scored_post(
                        post_data=post_data,
                        channel_title=channel_title,
                        username=username,
                        subscribers=subscribers,
                        comments=comments_map.get((username, post_data.get("id", 0)), 0),
                    )
                    post_with_channel = post_data.copy()
                    post_with_channel["channel"] = channel_info
                    post_with_channel["_score"] = scored_post.score(use_cache=True)
                    final_posts.append(post_with_channel)

                return final_posts
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Channel processing failed for %s: %s", username, exc)
                return []

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def _build_scored_post(
        self,
        post_data: dict[str, Any],
        channel_title: str,
        username: str,
        subscribers: int,
        comments: int,
    ) -> ScoredPost:
        """Convert raw post payload into score model."""
        footer = post_data.get("footer", {})
        post_content = post_data.get("content", {})
        reactions = (
            {}
            if self._simplified_parser
            else parse_reactions_single(post_content.get("reacts", []), parse_numeric_value)
        )

        return ScoredPost(
            post_id=post_data.get("id", 0),
            channel_name=channel_title,
            username=username,
            published_at=parse_datetime_fast(footer.get("date", {}).get("string", "")),
            views=parse_numeric_value(footer.get("views", "0"), 0),
            content=parse_content_type_single(post_content),
            edited=footer.get("edited", False),
            reactions=reactions,
            subscribers=subscribers,
            comments=comments,
        )
