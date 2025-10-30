import asyncio
import aiohttp
from datetime import datetime, timezone
import re
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

    def parse_subscribers(self, subscribers_str: Optional[str]) -> int:
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
        else:
            try:
                return int(subscribers_str)
            except (ValueError, TypeError):
                return 1

    def parse_views(self, views_str: Optional[str]) -> int:
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
        else:
            try:
                return int(views_str)
            except (ValueError, TypeError):
                return 0

    def parse_reactions(self, reacts_data: Optional[List[Dict]]) -> Dict[str, int]:
        """Convert reactions to dictionary"""
        reactions = {}
        if not reacts_data:
            return reactions

        for react in reacts_data:
            emoji = react.get('emoji', '')
            count_str = str(react.get('count', '0'))

            count = 0
            if 'k' in count_str.lower():
                try:
                    count = int(float(count_str.replace(
                        'K', '').replace('k', '')) * 1000)
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

    def parse_content_type(self, post_content: Dict[str, Any]) -> Dict[str, Any]:
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

    def parse_datetime(self, date_str: str) -> datetime:
        """Convert date string to datetime object"""
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc)

    async def fetch_comments_count(self, username: str, post_id: int, session: aiohttp.ClientSession) -> int:
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
                id=post_id,
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
    def __init__(self, id: int, channel: str, username: str, published_at: datetime,
                 views: int, content: Dict[str, Any], edited: bool = False,
                 reactions: Optional[Dict[str, int]] = None, subscribers: int = 1,
                 comments: int = 0):
        self.id = id
        self.channel = channel
        self.username = username
        self.published_at = published_at
        self.views = views
        self.content = content
        self.edited = edited
        self.reactions = reactions or {}
        self.subscribers = subscribers
        self.comments = comments

    def engagement_score(self) -> float:
        """Calculate engagement score based on reactions, comments, and views"""
        positive = ['👍', '❤', '❤️', '😂', '🔥', '💯', '👏', '🎉', '🥰']
        negative = ['👎', '😡', '😢', '🤬', '🤡']

        reaction_score = sum(self.reactions.get(r, 0) for r in positive) - \
            sum(self.reactions.get(r, 0) for r in negative)

        reaction_score /= max(self.subscribers, 1)
        comments_score = self.comments / max(self.subscribers, 1)
        views_score = self.views / max(self.subscribers, 1)

        return views_score + reaction_score + comments_score

    def content_score(self) -> float:
        """Calculate content score based on media types and text length"""
        weights = {'photos': 0.3, 'videos': 0.5, 'gifs': 0.2, 'poll': 0.3}
        score = 0.0
        for key, weight in weights.items():
            score += self.content.get(key, 0) * weight

        text_len = len(self.content.get('text', ''))
        score += min(text_len / 500, 1.0) * 0.1
        return score

    def freshness_score(self, current_time: Optional[datetime] = None) -> float:
        """Calculate freshness score based on time since publication"""
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        published_at = self.published_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        hours_since_post = (current_time - published_at).total_seconds() / 3600
        return 1 / (1 + hours_since_post / 24)

    def score(self, current_time: Optional[datetime] = None,
              weights: Optional[Dict[str, float]] = None) -> float:
        """Calculate overall post score"""
        weights = weights or {
            'engagement': 1.0,
            'content': 0.5,
            'freshness': 0.3,
            'edited': 0.1
        }

        score = (weights['engagement'] * self.engagement_score() +
                 weights['content'] * self.content_score() +
                 weights['freshness'] * self.freshness_score(current_time) +
                 (weights['edited'] if self.edited else 0))
        return score
