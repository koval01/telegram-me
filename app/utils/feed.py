import asyncio
import aiohttp
from datetime import datetime, timezone
import re
import math
from typing import List, Dict, Any, Optional
import logging

from app.telegram.telegram import Telegram

logger = logging.getLogger(__name__)


class PostDataPreparer:
    def __init__(self) -> None:
        self.telegram = Telegram()

    async def fetch_channel_data(self, username: str) -> Optional[Dict[str, Any]]:
        """Get channel data by username asynchronously"""
        try:
            return await self.telegram.body(username)

        except Exception as e:
            logger.error(f"Unexpected error fetching data for {username}: {e}")
            return None

    @staticmethod
    def parse_subscribers(subscribers_str: Optional[str]) -> int:
        """Convert subscriber string to number"""
        if not subscribers_str:
            return 1

        subscribers_str = subscribers_str.replace(" ", "").lower()

        if 'k' in subscribers_str:
            num_str = subscribers_str.replace('k', '')
            try:
                return int(float(num_str) * 1000)
            except (ValueError, TypeError):
                return 1
        elif 'm' in subscribers_str:
            num_str = subscribers_str.replace('m', '')
            try:
                return int(float(num_str) * 1000000)
            except (ValueError, TypeError):
                return 1
        else:
            try:
                return int(subscribers_str)
            except (ValueError, TypeError):
                return 1

    @staticmethod
    def parse_views(views_str: Optional[str]) -> int:
        """Convert views string to number"""
        if not views_str:
            return 0

        views_str = views_str.replace(" ", "").lower()

        if 'k' in views_str:
            num_str = views_str.replace('k', '')
            try:
                return int(float(num_str) * 1000)
            except (ValueError, TypeError):
                return 0
        elif 'm' in views_str:
            num_str = views_str.replace('m', '')
            try:
                return int(float(num_str) * 1000000)
            except (ValueError, TypeError):
                return 0
        else:
            try:
                return int(views_str)
            except (ValueError, TypeError):
                return 0

    @staticmethod
    def parse_reactions(reacts_data: Optional[List[Dict]]) -> Dict[str, int]:
        """Convert reactions to dictionary"""
        reactions = {}
        if not reacts_data:
            return reactions

        for react in reacts_data:
            emoji = react.get('emoji', '')
            count_str = str(react.get('count', '0'))

            if 'k' in count_str.lower():
                try:
                    count = int(float(count_str.replace(
                        'K', '').replace('k', '')) * 1000)
                except (ValueError, TypeError):
                    count = 0
            elif 'm' in count_str.lower():
                try:
                    count = int(float(count_str.replace(
                        'M', '').replace('m', '')) * 1000000)
                except (ValueError, TypeError):
                    count = 0
            else:
                try:
                    count = int(count_str)
                except (ValueError, TypeError):
                    count = 0

            if emoji and count > 0:
                reactions[emoji] = count

        return reactions

    @staticmethod
    def parse_content_type(post_content: Dict[str, Any]) -> Dict[str, Any]:
        """Determine content type in post"""
        content = {
            'text': '',
            'photos': 0,
            'videos': 0,
            'gifs': 0,
            'poll': 0
        }

        if 'text' in post_content and post_content['text']:
            text_data = post_content['text']
            if isinstance(text_data, dict) and 'string' in text_data:
                content['text'] = text_data['string']
            elif isinstance(text_data, str):
                content['text'] = text_data

        if 'media' in post_content and post_content['media']:
            for media in post_content['media']:
                media_type = media.get('type', '')
                if media_type == 'image':
                    content['photos'] += 1
                elif media_type == 'video':
                    content['videos'] += 1
                elif media_type == 'sticker':
                    content['gifs'] += 1

        return content

    @staticmethod
    def parse_datetime(date_str: str) -> datetime:
        """Convert date string to datetime object"""
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc)

    @staticmethod
    async def fetch_comments_count(username: str, post_id: int, session: aiohttp.ClientSession) -> int:
        """Fetch comments count asynchronously"""
        try:
            async with session.get(
                    f"https://t.me/{username}/{post_id}",
                    params={"embed": 1, "discussion": 1, "comments_limit": 3}
            ) as response:
                text = await response.text()
                _match = re.search(
                    r'<span class="js-header">(\d+)\s+comments?</span>', text)
                return int(_match.group(1)) if _match else 0
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.warning(
                f"Error fetching comments for {username}/{post_id}: {e}")
            return 0

    async def process_single_post(self, post_data: Dict[str, Any], channel_info: Dict[str, Any],
                                  username: str, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        """Process a single post asynchronously"""
        try:
            counters = channel_info.get('counters', {})
            subscribers = self.parse_subscribers(
                counters.get('subscribers', '1'))

            post_id = post_data.get('id', 0)
            footer = post_data.get('footer', {})
            post_content = post_data.get('content', {})

            comments_task = asyncio.create_task(
                self.fetch_comments_count(username, post_id, session)
            )

            content_dict = self.parse_content_type(post_content)
            reactions = self.parse_reactions(post_content.get('reacts', []))
            views = self.parse_views(footer.get('views', '0'))

            date_info = footer.get('date', {})
            published_at = self.parse_datetime(date_info.get('string', ''))
            edited = footer.get('edited', False)

            comments = await comments_task

            post_obj = Post(
                _id=post_id,
                channel=channel_info.get('title', {}).get('string', username),
                username=username,
                published_at=published_at,
                views=views,
                content=content_dict,
                edited=edited,
                reactions=reactions,
                subscribers=subscribers,
                comments=comments
            )

            post_with_channel = post_data.copy()
            post_with_channel['channel'] = channel_info
            post_with_channel['_score'] = post_obj.score()

            return post_with_channel

        except Exception as e:
            logger.error(
                f"Error processing post {post_data.get('id', 'unknown')}: {e}")
            return None

    async def prepare_posts_from_channel(self, username: str, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        """Prepare posts from channel asynchronously"""
        channel_data = await self.fetch_channel_data(username)
        if not channel_data or 'content' not in channel_data or 'posts' not in channel_data['content']:
            return []

        channel_info = channel_data.get('channel', {})
        posts_data = channel_data['content']['posts']

        tasks = [
            self.process_single_post(
                post_data, channel_info, username, session)
            for post_data in posts_data
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_posts = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Task failed with exception: {result}")
            elif result is not None:
                valid_posts.append(result)

        return valid_posts

    async def prepare_multiple_channels(self, usernames: List[str]) -> List[Dict[str, Any]]:
        """Prepare posts from multiple channels asynchronously"""
        all_posts = []

        async with aiohttp.ClientSession() as session:
            tasks = [
                self.prepare_posts_from_channel(username, session)
                for username in usernames
            ]

            channel_results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(channel_results):
                username = usernames[i]
                if isinstance(result, Exception):
                    logger.error(
                        f"Failed to process channel {username}: {result}")
                else:
                    all_posts.extend(result)
                    logger.info(f"Loaded {len(result)} posts from {username}")

        return all_posts


class Post:
    def __init__(self, _id: int, channel: str, username: str, published_at: datetime,
                 views: int, content: Dict[str, Any], edited: bool = False,
                 reactions: Optional[Dict[str, int]] = None, subscribers: int = 1,
                 comments: int = 0):
        self.id = _id
        self.channel = channel
        self.username = username
        self.published_at = published_at
        self.views = views
        self.content = content
        self.edited = edited
        self.reactions = reactions or {}
        self.subscribers = max(subscribers, 1)  # Ensure at least 1
        self.comments = comments

    def engagement_score(self) -> float:
        """Calculate engagement score based on reactions, comments, and views"""
        positive = ['👍', '❤', '❤️', '😂', '🔥', '💯', '👏', '🎉', '🥰']
        negative = ['👎', '😡', '😢', '🤬', '🤡']

        # Calculate raw reaction counts
        positive_reactions = sum(self.reactions.get(r, 0) for r in positive)
        negative_reactions = sum(self.reactions.get(r, 0) for r in negative)

        # Use percentages instead of raw counts to avoid huge numbers
        reaction_percentage = (positive_reactions - negative_reactions) / max(self.subscribers, 1)
        comments_percentage = self.comments / max(self.subscribers, 1)
        views_percentage = self.views / max(self.subscribers, 1)

        # Normalize to reasonable ranges
        engagement_score = (
                min(views_percentage, 10.0) +  # Cap views at 1000% of subscribers
                min(reaction_percentage, 1.0) +  # Cap reactions at 100% of subscribers
                min(comments_percentage, 0.5)  # Cap comments at 50% of subscribers
        )

        return engagement_score / 3.0  # Normalize to roughly 0-4 range

    def content_score(self) -> float:
        """Calculate content score based on media types and text length"""
        weights = {'photos': 0.3, 'videos': 0.5, 'gifs': 0.2, 'poll': 0.3}

        # Cap media counts to prevent excessive scoring
        media_bonus = 0
        for key, weight in weights.items():
            media_count = min(self.content.get(key, 0), 10)  # Cap at 10 per type
            media_bonus += media_count * weight

        # Text length bonus (capped)
        text_len = len(self.content.get('text', ''))
        text_bonus = min(text_len / 500, 2.0) * 0.1  # Cap at 2.0

        return min(media_bonus + text_bonus, 3.0)  # Cap total content score

    def freshness_score(self, current_time: Optional[datetime] = None) -> float:
        """Extremely aggressive exponential decay to heavily penalize old posts"""
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        published_at = self.published_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        hours_since_post = (current_time - published_at).total_seconds() / 3600

        # Ultra-aggressive decay - posts older than 2 weeks get nearly zero score
        if hours_since_post <= 24:
            # First 24 hours: linear decay from 1.0 to 0.8
            return 1.0 - (0.2 * (hours_since_post / 24))
        elif hours_since_post <= 168:  # 1 week
            # Week 1: exponential decay from 0.8 to 0.1
            days = hours_since_post / 24
            return 0.8 * math.exp(-0.8 * (days - 1))
        else:
            # After 1 week: extremely aggressive decay
            weeks = hours_since_post / 168
            return max(0.001, 0.1 * math.exp(-2.0 * (weeks - 1)))

    def score(self, current_time: Optional[datetime] = None,
              weights: Optional[Dict[str, float]] = None) -> float:
        """Calculate overall post score with extreme time sensitivity"""
        weights = weights or {
            'engagement': 0.4,  # Reduced
            'content': 0.2,  # Reduced
            'freshness': 0.4,  # Increased significantly - now the most important factor
            'edited': 0.02  # Minimal impact
        }

        # Calculate component scores
        engagement = self.engagement_score()
        content = self.content_score()
        freshness = self.freshness_score(current_time)
        edited_bonus = weights['edited'] if self.edited else 0

        # Apply freshness as a multiplier to heavily penalize old content
        base_score = (
                weights['engagement'] * engagement +
                weights['content'] * content +
                edited_bonus
        )

        # Use freshness as multiplier for extreme time sensitivity
        raw_score = base_score * (freshness + 0.1)  # +0.1 to avoid zero multiplication

        # Apply final cap
        return min(raw_score, 10.0)
