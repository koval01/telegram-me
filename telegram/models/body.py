from __future__ import annotations
from typing import List, Optional

from pydantic import BaseModel

from telegram.models.post import Post
from telegram.models.meta import Meta


class Channel(BaseModel):
    username: str
    title: str
    description: str
    avatar: str


class Counter(BaseModel):
    subscribers: str
    photos: Optional[str] = None
    videos: Optional[str] = None
    files: Optional[str] = None
    links: Optional[str] = None


class Content(BaseModel):
    counters: List[Counter]
    posts: List[Post]


class ChannelBody(BaseModel):
    channel: Channel
    content: Content
    meta: Meta
