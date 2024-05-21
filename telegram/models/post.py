"""
Model for message (post) container
"""

from __future__ import annotations
from typing import List, Optional, Literal

from pydantic import BaseModel


class Duration(BaseModel):
    """
    Represents the duration of a media item.

    Attributes:
        formatted (str): The formatted duration string.
        raw (int): The duration in seconds.
    """
    formatted: str
    raw: int


class MediaItem(BaseModel):
    """
    Represents a media item within a post.

    Attributes:
        url (str): The URL of the media item.
        thumb (Optional[str]): The URL of the thumbnail image (if applicable).
        waves (Optional[str]): The URL of the waveform image (if applicable).
        duration (Optional[Duration]): The duration of the media item (if applicable).
        type (Literal["image", "video", "voice", "roundvideo", "sticker", "gif"]):
        The type of the media item.
    """
    url: str
    thumb: Optional[str] = None
    waves: Optional[str] = None
    duration: Optional[Duration] = None
    type: Literal["image", "video", "voice", "roundvideo", "sticker", "gif"]


class TextEntities(BaseModel):
    """
    Represents text entities within a text.

    Attributes:
        offset (int): The offset of the entity in the text.
        length (int): The length of the entity.
        type (Literal[
            "italic", "bold", "code", "spoiler", "strikethrough",
            "underline", "text_link", "url", "pre"
        ]):
        The type of the entity.
        language (Optional[str]): The language of the entity (if applicable).
    """
    offset: int
    length: int
    type: Literal[
        "italic", "bold", "code", "spoiler", "strikethrough",
        "underline", "text_link", "url", "pre", "emoji"
    ]
    language: Optional[str]


class Text(BaseModel):
    """
    Represents a text content within a post.

    Attributes:
        string (str): The text string.
        entities (Optional[List[TextEntities]]):
            List of text entities within the text.
    """
    string: str
    entities: Optional[List[TextEntities]]


class PollOptions(BaseModel):
    """
    Represents options within a poll.

    Attributes:
        name (str): The name of the option.
        percent (int): The percentage of votes for the option.
    """
    name: str
    percent: int


class Poll(BaseModel):
    """
    Represents a poll within a post.

    Attributes:
        name (str): The name of the poll.
        type (Optional[str]): The type of the poll.
        votes (str): The total number of votes in the poll.
        options (List[PollOptions]): List of options within the poll.
    """
    name: str
    type: Optional[str] = None
    votes: str
    options: List[PollOptions]


class Inline(BaseModel):
    """
    Represents a inline block of post.

    Attributes:
        title (str): The title inline button.
        url (str): Redirect link for button.
    """
    title: str
    url: str


class ContentPost(BaseModel):
    """
    Represents the content of a post.

    Attributes:
        text (Optional[Text]): The text content of the post.
        media (Optional[List[MediaItem]]): List of media items within the post.
        poll (Optional[Poll]): The poll within the post.
    """
    text: Optional[Text] = None
    media: Optional[List[MediaItem]] = None
    poll: Optional[Poll] = None
    inline: Optional[Inline] = None


class Date(BaseModel):
    """
    Represents a date.

    Attributes:
        string (str): The string representation of the date.
        unix (int): The Unix timestamp of the date.
    """
    string: str
    unix: int


class Footer(BaseModel):
    """
    Represents the footer of a post.

    Attributes:
        views (Optional[str]): The number of views of the post.
        author (Optional[str]): Post's auth name.
        date (Date): The date of the post.
    """
    views: Optional[str]
    author: Optional[str]
    date: Date


class Forwarded(BaseModel):
    """
    Represents forwarded information within a post.

    Attributes:
        name (str): The name of the source where the post was forwarded from.
        url (str): The URL of the source where the post was forwarded from.
    """
    name: str
    url: str


class Post(BaseModel):
    """
    Represents a post in a telegram channel.

    Attributes:
        id (int): The unique identifier of the post.
        content (ContentPost): The content of the post.
        footer (Footer): The footer of the post.
        forwarded (Optional[Forwarded]): Information about forwarded post (if applicable).
    """
    id: int
    content: ContentPost
    footer: Footer
    forwarded: Optional[Forwarded] = None
