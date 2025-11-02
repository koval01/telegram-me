"""
Telegram main module
"""
import asyncio
from typing import Literal
import json
import redis.asyncio as redis

from app.telegram.parser.methods.body import Body
from app.telegram.parser.methods.more import More
from app.telegram.parser.methods.preview import Preview

from app.telegram.request import Request
from app.utils.config import settings


class Telegram:
    """
    A class representing a simplified interface for
    interacting with the Telegram API.

    Methods:
        body: Retrieve message bodies from a Telegram channel.
        more: Retrieve additional messages from a Telegram channel.
    """

    def __init__(self) -> None:
        self._redis_client = None

    async def _get_redis_client(self) -> redis.Redis:
        """Get Redis client connection (lazy initialization)"""
        if self._redis_client is None:
            self._redis_client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True
            )
        return self._redis_client

    @staticmethod
    async def body(channel: str, position: int = 0) -> dict:
        """
        Retrieve the body of a message from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The position of the message in the channel's history.
            Default to 0 (most recent).

        Returns:
            dict: A dictionary containing the message body.
        """
        response = await Request().body(channel, position)
        if not response:
            return {}

        return Body(response).get()

    @staticmethod
    async def more(
        channel: str, position: int, direction: Literal["after", "before"]
    ) -> dict:
        """
        Retrieve additional messages from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The position of the message in the channel's history.
            direction (Literal["after", "before"]):
            The direction of message retrieval relative to the
            specified position.

        Returns:
            dict: A dictionary containing the additional messages.
        """
        response = await Request().more(channel, position, direction)

        if isinstance(response, str) and response == "":
            return More(response).get()

        if not response:
            return {}

        # additional validation response
        response = More(response).get()
        response["posts"] = list(
            filter(lambda post: post["id"] != position, response["posts"])
        )
        return response

    @staticmethod
    async def post(channel: str, position: int, only_post: bool = True) -> dict:
        """
        Retrieve a specific post from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The ID of the post to retrieve.
            only_post (bool): Get only a Post object

        Returns:
            dict: A dictionary containing the requested post if found,
            otherwise an empty dictionary.
        """
        response = await Request().body(channel, position)
        if not response:
            return {}

        response = Body(response).get(position)
        if not response["content"]["posts"]:
            return {}
        if only_post:
            response = response["content"]["posts"][0]

        return response

    async def preview(self, channel: str, cache_ttl: int = 7200) -> dict:
        """
        Retrieve preview information of channel.

        Args:
            channel (str): The channel name or ID.
            cache_ttl (int): Cache time-to-live in seconds (default: 1 hour)

        Returns:
            dict: A dictionary containing the main channel information.
        """
        redis_client = await self._get_redis_client()
        cache_key = f"telegram:preview:{channel}"

        # Try to get from cache first
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as e:
            # Log cache error but continue to fetch from API
            print(f"Cache error: {e}")

        # Fetch from API if not in cache
        response = await Request().preview(channel)
        if not response:
            return {}

        preview_data = Preview(response).get()

        # Cache the result
        try:
            await redis_client.setex(
                cache_key,
                cache_ttl,
                json.dumps(preview_data)
            )
        except Exception as e:
            # Log cache error but return the data
            print(f"Cache set error: {e}")

        return preview_data

    async def previews(self, channels: list[str], cache_ttl: int = 3600) -> dict:
        """
        Obtaining a group of channel previews

        Args:
            channels (list[str]): List of channels requested
            cache_ttl (int): Cache time-to-live in seconds (default: 1 hour)

        Returns:
            dict: A dictionary that contains a list of requested channels,
                their usernames are used as keys
        """
        # Remove duplicates while preserving order
        unique_channels = list(dict.fromkeys(channels))

        tasks = [self.preview(channel, cache_ttl) for channel in unique_channels]
        results = await asyncio.gather(*tasks)
        return dict(zip(unique_channels, results))

    async def clear_preview_cache(self, channel: str = None) -> None:
        """
        Clear cache for specific channel or all preview caches

        Args:
            channel (str, optional): Specific channel to clear cache for.
                                   If None, clear all preview caches.
        """
        redis_client = await self._get_redis_client()

        try:
            if channel:
                cache_key = f"telegram:preview:{channel}"
                await redis_client.delete(cache_key)
            else:
                # Clear all preview caches
                keys = await redis_client.keys("telegram:preview:*")
                if keys:
                    await redis_client.delete(*keys)
        except Exception as e:
            print(f"Cache clear error: {e}")

    async def close(self) -> None:
        """Close Redis connection"""
        if self._redis_client:
            await self._redis_client.close()
            self._redis_client = None
