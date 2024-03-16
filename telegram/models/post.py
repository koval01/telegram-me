from __future__ import annotations
from typing import List, Optional, Literal

from pydantic import BaseModel


class Duration(BaseModel):
    formatted: str
    raw: int


class MediaItem(BaseModel):
    url: str
    thumb: Optional[str] = None
    waves: Optional[str] = None
    duration: Optional[Duration] = None
    type: Literal["image", "video", "voice", "roundvideo"]


class TextEntities(BaseModel):
    offset: int
    length: int
    type: Literal["italic", "bold", "code", "spoiler", "strikethrough", "underline", "text_link", "url", "pre"]
    language: Optional[str]


class Text(BaseModel):
    string: str
    entities: List[TextEntities]


class PollOptions(BaseModel):
    name: str
    percent: int


class Poll(BaseModel):
    name: str
    type: Optional[str] = None
    votes: str
    options: List[PollOptions]


class ContentPost(BaseModel):
    text: Optional[Text] = None
    media: Optional[List[MediaItem]] = None
    poll: Optional[Poll] = None


class Date(BaseModel):
    string: str
    unix: int


class Footer(BaseModel):
    views: Optional[str]
    date: Date


class Forwarded(BaseModel):
    name: str
    url: str


class Post(BaseModel):
    id: int
    content: ContentPost
    footer: Footer
    forwarded: Optional[Forwarded] = None
