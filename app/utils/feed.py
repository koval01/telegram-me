"""
ULTRA-optimized module for Telegram channel posts preparation.
"""

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import aiohttp
from aiohttp import TCPConnector
import uvloop

from app.telegram.telegram import Telegram

try:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Extreme connection pooling
CONNECTOR_SETTINGS = {
    "limit": 200,
    "limit_per_host": 50,
    "keepalive_timeout": 60,
    "use_dns_cache": True,
    "ttl_dns_cache": 300,
}

# Global session to avoid recreation
_global_session = None

def get_global_session():
    global _global_session
    if _global_session is None:
        connector = TCPConnector(**CONNECTOR_SETTINGS)
        timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
        _global_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _global_session


class PostDataPreparer:
    """ULTRA-optimized post preparer with radical performance improvements."""

    def __init__(self) -> None:
        self.telegram = Telegram()
        # Pre-compile ALL regex patterns
        self._comments_pattern = re.compile(r'js-header[^>]*>(\d+)\s*comment', re.IGNORECASE)
        self._number_clean_pattern = re.compile(r'[^\dkkm.]', re.IGNORECASE)

        # Cache with TTL
        self._channel_cache = {}
        self._comment_cache = {}
        self._cache_ttl = 60  # 1 minute

    async def fetch_channel_data_batch(self, usernames: List[str]) -> Dict[str, Any]:
        """Fetch multiple channels in parallel with aggressive timeout."""
        tasks = {}
        for username in usernames:
            cache_key = username.lower()
            if cache_key in self._channel_cache:
                data, timestamp = self._channel_cache[cache_key]
                if asyncio.get_event_loop().time() - timestamp < self._cache_ttl:
                    continue

            # Use asyncio.wait_for with aggressive timeout
            task = asyncio.wait_for(
                self.telegram.body(username),
                timeout=8.0  # Aggressive timeout
            )
            tasks[username] = task

        # Execute all remaining fetches in parallel
        if tasks:
            results = await asyncio.gather(
                *tasks.values(),
                return_exceptions=True
            )

            # Update cache
            current_time = asyncio.get_event_loop().time()
            for username, result in zip(tasks.keys(), results):
                if not isinstance(result, Exception) and result:
                    self._channel_cache[username.lower()] = (result, current_time)

        # Return all data (cached + fresh)
        channel_data = {}
        for username in usernames:
            cache_key = username.lower()
            if cache_key in self._channel_cache:
                channel_data[username] = self._channel_cache[cache_key][0]

        return channel_data

    async def fetch_comments_count_ultra_batch(
        self,
        post_infos: List[Tuple[str, int]],
        session: aiohttp.ClientSession
    ) -> Dict[Tuple[str, int], int]:
        """ULTRA-optimized batch comment fetching."""
        if not post_infos:
            return {}

        # Check cache first
        current_time = asyncio.get_event_loop().time()
        results = {}
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

        # Fetch remaining posts with extreme batching
        BATCH_SIZE = 20  # Larger batches
        all_batch_results = {}

        for i in range(0, len(remaining_posts), BATCH_SIZE):
            batch = remaining_posts[i:i + BATCH_SIZE]
            batch_results = await self._fetch_comment_batch_with_fallback(batch, session)
            all_batch_results.update(batch_results)

            # Small delay to avoid rate limiting, but much smaller
            if i + BATCH_SIZE < len(remaining_posts):
                await asyncio.sleep(0.05)  # 50ms instead of longer

        # Update cache and results
        for post_info, count in all_batch_results.items():
            cache_key = f"{post_info[0]}/{post_info[1]}"
            self._comment_cache[cache_key] = (count, current_time)
            results[post_info] = count

        return results

    async def _fetch_comment_batch_with_fallback(
        self,
        post_infos: List[Tuple[str, int]],
        session: aiohttp.ClientSession
    ) -> Dict[Tuple[str, int], int]:
        """Fetch batch with fallback to individual requests if batch fails."""
        try:
            # Try ultra-fast parallel fetching
            tasks = []
            for username, post_id in post_infos:
                url = f"https://t.me/{username}/{post_id}?embed=1&discussion=1&comments_limit=1"
                task = asyncio.wait_for(
                    self._fetch_single_comment_fast(url, session),
                    timeout=6.0
                )
                tasks.append(task)

            counts = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            results = {}
            for i, (post_info, count) in enumerate(zip(post_infos, counts)):
                if isinstance(count, Exception):
                    results[post_info] = 0
                else:
                    results[post_info] = count

            return results

        except Exception as e:
            logger.warning("Batch comment fetch failed, using zeros: %s", e)
            return {post_info: 0 for post_info in post_infos}

    async def _fetch_single_comment_fast(self, url: str, session: aiohttp.ClientSession) -> int:
        """Ultra-optimized single comment fetch."""
        try:
            async with session.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Telegram-Fetcher/1.0)',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            }) as response:
                # Only read the first 10KB (comments count is usually in the first few KB)
                text = await response.text()#[:10240]  # Commented out to avoid partial HTML issues
                match = self._comments_pattern.search(text)
                return int(match.group(1)) if match else 0

        except asyncio.TimeoutError:
            return 0
        except Exception:
            return 0

    @staticmethod
    def parse_content_type_batch(posts_content: List[Dict]) -> List[Dict]:
        """Batch process content types for better performance."""
        results = []
        for content in posts_content:
            processed = {
                'text': '',
                'photos': 0,
                'videos': 0,
                'gifs': 0,
                'poll': 0
            }

            text_data = content.get('text')
            if text_data:
                if isinstance(text_data, dict):
                    processed['text'] = text_data.get('string', '')
                else:
                    processed['text'] = str(text_data)

            media_list = content.get('media', [])
            for media in media_list:
                media_type = media.get('type', '')
                if media_type == 'image':
                    processed['photos'] += 1
                elif media_type == 'video':
                    processed['videos'] += 1
                elif media_type == 'sticker':
                    processed['gifs'] += 1

            results.append(processed)

        return results

    async def prepare_multiple_channels(
        self,
        usernames: List[str],
        max_concurrent: int = 8
    ) -> List[Dict[str, Any]]:
        """ULTRA-fast multiple channel processing."""
        if not usernames:
            return []

        session = get_global_session()

        # Step 1: Fetch ALL channel data in parallel
        logger.info("Fetching %d channels in parallel", len(usernames))
        channel_data_map = await self.fetch_channel_data_batch(usernames)

        # Step 2: Prepare ALL posts from ALL channels in parallel
        all_tasks = []
        semaphore = asyncio.Semaphore(max_concurrent)

        for username in usernames:
            if username in channel_data_map:
                task = self._process_channel_posts_ultra_fast(
                    username, channel_data_map[username], session, semaphore
                )
                all_tasks.append(task)

        # Wait for ALL channels
        channel_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Combine results
        all_posts = []
        for result in channel_results:
            if isinstance(result, Exception):
                logger.error("Channel processing failed: %s", result)
            elif result:
                all_posts.extend(result)

        logger.info("ULTRA-fast processing completed: %d total posts", len(all_posts))
        return all_posts

    async def _process_channel_posts_ultra_fast(
        self,
        username: str,
        channel_data: Dict[str, Any],
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore
    ) -> List[Dict[str, Any]]:
        """ULTRA-fast single channel processing."""
        async with semaphore:
            try:
                if 'content' not in channel_data or 'posts' not in channel_data['content']:
                    return []

                channel_info = channel_data.get('channel', {})
                posts_data = channel_data['content']['posts']

                if not posts_data:
                    return []

                # Pre-calculate shared values
                counters = channel_info.get('counters', {})
                subscribers = self.parse_numeric_value(counters.get('subscribers', '1'), 1)
                channel_title = channel_info.get('title', {}).get('string', username)

                # Prepare comment fetching
                post_infos = [(username, post.get('id', 0)) for post in posts_data]
                comments_task = asyncio.create_task(
                    self.fetch_comments_count_ultra_batch(post_infos, session)
                )

                # Process ALL posts locally (extremely fast)
                processed_posts = []
                for i, post_data in enumerate(posts_data):
                    post_obj = await self._process_post_instant(
                        post_data, channel_title, username, subscribers
                    )
                    processed_posts.append((i, post_obj))

                # Get comments and combine
                comments_map = await comments_task

                # Finalize posts
                final_posts = []
                for i, post_obj in processed_posts:
                    post_id = posts_data[i].get('id', 0)
                    comments = comments_map.get((username, post_id), 0)
                    post_obj.comments = comments

                    post_with_channel = posts_data[i].copy()
                    post_with_channel['channel'] = channel_info
                    post_with_channel['_score'] = post_obj.score(use_cache=True)

                    final_posts.append(post_with_channel)

                return final_posts

            except Exception as e:
                logger.error("ULTRA-fast processing failed for %s: %s", username, e)
                return []

    async def _process_post_instant(
        self,
        post_data: Dict[str, Any],
        channel_title: str,
        username: str,
        subscribers: int
    ) -> 'Post':
        """Instant post processing (no I/O)."""
        post_id = post_data.get('id', 0)
        footer = post_data.get('footer', {})
        post_content = post_data.get('content', {})

        # Fast content parsing
        content_dict = self.parse_content_type_single(post_content)
        reactions = self.parse_reactions_single(post_content.get('reacts', []))
        views = self.parse_numeric_value(footer.get('views', '0'), 0)

        # Fast datetime parsing
        date_info = footer.get('date', {})
        published_at = self.parse_datetime_fast(date_info.get('string', ''))
        edited = footer.get('edited', False)

        return Post(
            post_id=post_id,
            channel_name=channel_title,
            username=username,
            published_at=published_at,
            views=views,
            content=content_dict,
            edited=edited,
            reactions=reactions,
            subscribers=subscribers,
            comments=0  # Filled later
        )

    @staticmethod
    def parse_content_type_single(post_content: Dict) -> Dict:
        """Single post content parsing optimized."""
        processed = {'text': '', 'photos': 0, 'videos': 0, 'gifs': 0, 'poll': 0}

        text_data = post_content.get('text')
        if text_data:
            processed['text'] = str(text_data.get('string', '')) if isinstance(text_data, dict) else str(text_data)

        media_list = post_content.get('media', [])
        for media in media_list:
            media_type = media.get('type', '')
            if media_type == 'image':
                processed['photos'] += 1
            elif media_type == 'video':
                processed['videos'] += 1
            elif media_type == 'sticker':
                processed['gifs'] += 1

        return processed

    def parse_reactions_single(self, reacts_data: Optional[List]) -> Dict:
        """Single post reactions parsing optimized."""
        if not reacts_data:
            return {}

        reactions = {}
        for react in reacts_data:
            emoji = react.get('emoji', '')
            if emoji:
                count_str = str(react.get('count', '0'))
                count = self.parse_numeric_value(count_str, 0)
                if count > 0:
                    reactions[emoji] = count

        return reactions

    @staticmethod
    def parse_datetime_fast(date_str: str) -> datetime:
        """Ultra-fast datetime parsing."""
        try:
            if 'Z' in date_str:
                date_str = date_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(date_str)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc)

    @staticmethod
    def parse_numeric_value(value_str: Optional[str], default: int = 0) -> int:
        """Ultra-fast numeric parser."""
        if not value_str:
            return default

        cleaned = value_str.replace(' ', '').lower()

        if cleaned.isdigit():
            return int(cleaned)

        try:
            if 'k' in cleaned:
                return int(float(cleaned.replace('k', '')) * 1000)
            elif 'm' in cleaned:
                return int(float(cleaned.replace('m', '')) * 1000000)
            else:
                return int(cleaned)
        except (ValueError, TypeError):
            return default


class Post:
    """Optimized Post class with cached scoring."""

    __slots__ = (
        'post_id', 'channel_name', 'username', 'published_at',
        'views', 'content', 'edited', 'reactions', 'subscribers', 'comments',
        '_cached_score', '_score_timestamp'
    )

    # Pre-defined reaction categories for faster engagement calculation
    POSITIVE_REACTIONS = {'👍', '❤', '❤️', '😂', '🔥', '💯', '👏', '🎉', '🥰'}
    NEGATIVE_REACTIONS = {'👎', '😡', '😢', '🤬', '🤡'}

    def __init__(
            self,
            post_id: int,
            channel_name: str,
            username: str,
            published_at: datetime,
            views: int,
            content: Dict[str, Any],
            edited: bool = False,
            reactions: Optional[Dict[str, int]] = None,
            subscribers: int = 1,
            comments: int = 0
    ) -> None:
        """Initialize optimized Post instance."""
        self.post_id = post_id
        self.channel_name = channel_name
        self.username = username
        self.published_at = published_at
        self.views = views
        self.content = content
        self.edited = edited
        self.reactions = reactions or {}
        self.subscribers = max(subscribers, 1)
        self.comments = comments
        self._cached_score: Optional[float] = None
        self._score_timestamp: Optional[float] = None

    def engagement_score(self) -> float:
        """Optimized engagement score calculation."""
        subscribers = self.subscribers

        # Fast reaction counting using set lookups
        positive = sum(self.reactions.get(r, 0) for r in self.POSITIVE_REACTIONS)
        negative = sum(self.reactions.get(r, 0) for r in self.NEGATIVE_REACTIONS)

        # Calculate percentages with bounds checking
        reaction_pct = (positive - negative) / subscribers
        comments_pct = self.comments / subscribers
        views_pct = self.views / subscribers

        # Use min() for bounds checking (faster than if statements)
        engagement_score = (
                min(views_pct, 10.0) +
                min(reaction_pct, 1.0) +
                min(comments_pct, 0.5)
        )

        return engagement_score / 3.0

    def content_score(self) -> float:
        """Optimized content score calculation."""
        weights = {'photos': 0.3, 'videos': 0.5, 'gifs': 0.2, 'poll': 0.3}

        media_bonus = 0
        for key, weight in weights.items():
            media_count = min(self.content.get(key, 0), 10)
            media_bonus += media_count * weight

        # Fast text length calculation with bounds
        text_len = len(self.content.get('text', ''))
        text_bonus = min(text_len / 500, 2.0) * 0.1

        return min(media_bonus + text_bonus, 3.0)

    def freshness_score(self, current_time: Optional[datetime] = None) -> float:
        """Optimized freshness score with caching."""
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        # Ensure both times are timezone-aware
        published_at = self.published_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        hours_since_post = (current_time - published_at).total_seconds() / 3600

        # Optimized decay calculation
        if hours_since_post <= 24:
            return 1.0 - (0.2 * (hours_since_post / 24))
        elif hours_since_post <= 168:
            days = hours_since_post / 24
            return 0.8 * math.exp(-0.8 * (days - 1))
        else:
            weeks = hours_since_post / 168
            return max(0.001, 0.1 * math.exp(-2.0 * (weeks - 1)))

    def score(
            self,
            current_time: Optional[datetime] = None,
            weights: Optional[Dict[str, float]] = None,
            use_cache: bool = True
    ) -> float:
        """Optimized scoring with optional caching."""
        # Use cached score if available and recent
        if use_cache and self._cached_score is not None:
            cache_time = getattr(self, '_score_timestamp', 0)
            if asyncio.get_event_loop().time() - cache_time < 60:  # Cache for 60 seconds
                return self._cached_score

        weights = weights or {
            'engagement': 0.4,
            'content': 0.2,
            'freshness': 0.4,
            'edited': 0.02
        }

        # Calculate scores
        engagement = self.engagement_score()
        content = self.content_score()
        freshness = self.freshness_score(current_time)
        edited_bonus = weights['edited'] if self.edited else 0

        base_score = (
                weights['engagement'] * engagement +
                weights['content'] * content +
                edited_bonus
        )

        raw_score = base_score * (freshness + 0.1)
        final_score = min(raw_score, 10.0)

        # Cache the result
        if use_cache:
            self._cached_score = final_score
            self._score_timestamp = asyncio.get_event_loop().time()

        return final_score
