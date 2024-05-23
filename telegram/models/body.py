"""
Model for body response container
"""

from __future__ import annotations
from typing import List, Optional

from pydantic import BaseModel

from telegram.models.post import Post
from telegram.models.meta import Meta


class Channel(BaseModel):
    """
    Represents a Telegram channel.

    Attributes:
        username (str): The username of the channel.
        title (str): The title of the channel.
        description (str): The description of the channel.
        avatar (str): The URL of the channel's avatar.
        counters (List[Counter]): List of counters associated with the channel.
    """
    username: str
    title: str
    description: str
    avatar: str
    counters: List[Counter]


class Counter(BaseModel):
    """
    Represents counters associated with a Telegram channel.

    Attributes:
        subscribers (str): The number of subscribers.
        photos (Optional[str]): The number of photos (if available).
        videos (Optional[str]): The number of videos (if available).
        files (Optional[str]): The number of files (if available).
        links (Optional[str]): The number of links (if available).
    """
    subscribers: str
    photos: Optional[str] = None
    videos: Optional[str] = None
    files: Optional[str] = None
    links: Optional[str] = None


class Content(BaseModel):
    """
    Represents the content associated with a Telegram channel.

    Attributes:
        posts (List[Post] | Post): List of posts or selected post in the channel.
    """
    posts: List[Post] | Post


class ChannelBody(BaseModel):
    """
    Represents the body of a Telegram channel.

    Attributes:
        channel (Channel): Information about the channel.
        content (Content): Content associated with the channel.
        meta (Meta): Metadata associated with the channel content.
    """
    channel: Channel
    content: Content
    meta: Meta
