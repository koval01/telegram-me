from __future__ import annotations
from typing import List, Optional, Literal

from pydantic import BaseModel, HttpUrl

from app.telegram.models.utils import ParsedAndRaw


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
        url (Optional[HttpUrl]): The URL of the media item.
        thumb (Optional[str]): The URL of the thumbnail image (if applicable).
        waves (Optional[str]): The URL of the waveform image (if applicable).
        duration (Optional[Duration]): The duration of the media item (if applicable).
        type (Literal["image", "video", "voice", "roundvideo", "sticker", "gif"]):
        The type of the media item.
        available (Optional[bool]): Availability status of the media file itself
    """

    url: Optional[HttpUrl] = None
    thumb: Optional[HttpUrl] = None
    waves: Optional[str] = None
    duration: Optional[Duration] = None
    type: Literal["image", "video", "voice", "roundvideo", "sticker", "gif"]
    available: Optional[bool] = None

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
        "italic",
        "bold",
        "code",
        "spoiler",
        "strikethrough",
        "underline",
        "text_link",
        "url",
        "pre",
        "emoji",
        "animoji",
        "hashtag"
    ]
    language: Optional[str] = None


class Text(BaseModel):
    """
    Represents a text content within a post.

    Attributes:
        string (str): The text string.
        html (str): Original html.
        entities (Optional[List[TextEntities]]):
            List of text entities within the text.
    """

    string: str
    html: str
    entities: Optional[List[TextEntities]] = None


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
        question (str): The question of the poll.
        type (Optional[str]): The type of the poll.
        votes (str): The total number of votes in the poll.
        options (List[PollOptions]): List of options within the poll.
    """

    question: str
    type: Optional[str] = None
    votes: str
    options: List[PollOptions]


class Inline(BaseModel):
    """
    Represents a inline block of post.

    Attributes:
        title (str): The title inline button.
        url (HttpUrl): Redirect link for button.
    """

    title: str
    url: HttpUrl


class Reply(BaseModel):
    """
    Represents a reply to a post.

    Attributes:
        cover (Optional[HttpUrl]): The URL of the cover image for the reply (if applicable).
        name (ParsedAndRaw): The name of the user or channel making the reply,
            represented both in parsed and raw formats.
        text (ParsedAndRaw): The text content of the reply, represented both in parsed
            and raw formats.
        to_message (int): ID replied message
    """

    cover: Optional[HttpUrl]
    name: ParsedAndRaw
    text: ParsedAndRaw
    to_message: int


class PreviewLink(BaseModel):
    """
    Represents a preview link block within a post.

    Attributes:
        title (Optional[str]): The title of the linked content, if available
        url (HttpUrl): This URL of preview page
        site_name (Optional[str]): The name of the website or platform the link is from
        description (Optional[ParsedAndRaw]): The description of the linked content,
            containing both parsed text and raw HTML formats
        thumb (Optional[HttpUrl]): The URL of the preview thumbnail image
    """

    title: Optional[str]
    url: HttpUrl
    site_name: Optional[str]
    description: Optional[ParsedAndRaw]
    thumb: Optional[HttpUrl]


class ContentPost(BaseModel):
    """
    Represents the content of a post.

    Attributes:
        text (Optional[Text]): The text content of the post.
        media (Optional[List[MediaItem]]): List of media items within the post.
        poll (Optional[Poll]): The poll within the post.
        inline (Optional[List[Inline]]): List of inline buttons.
        reply (Optional[Reply]): Reply information.
        preview_link (Optional[PreviewLink]): Preview link information.
    """

    text: Optional[Text] = None
    media: Optional[List[MediaItem]] = None
    poll: Optional[Poll] = None
    inline: Optional[List[Inline]] = None
    reply: Optional[Reply] = None
    preview_link: Optional[PreviewLink] = None


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
        edited (Optional[bool]): Edited post's status.
        author (Optional[str]): Post's auth name.
        date (Date): The date of the post.
    """

    views: Optional[str]
    edited: Optional[bool]
    author: Optional[ParsedAndRaw] = None
    date: Date


class Forwarded(BaseModel):
    """
    Represents forwarded information within a post.

    Attributes:
        name (ParsedAndRaw): The name of the source where the post was forwarded from.
        url (Optional[HttpUrl]): The URL of the source where the post was forwarded from.
    """

    name: ParsedAndRaw
    url: Optional[HttpUrl] = None


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
    view: str
