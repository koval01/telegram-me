from __future__ import annotations
from typing import List, Optional

from pydantic import BaseModel, field_validator, HttpUrl, model_validator

from app.telegram.models.post import Post
from app.telegram.models.meta import Meta
from app.telegram.models.utils import ParsedAndRaw


class Labels(BaseModel):
    """Represents a collection of validation labels for Telegram channels.

    Attributes:
        labels (List[str]): List of channel verification labels.
            Currently only accepts "verified" as a valid label.

    Raises:
        ValueError: If any label in the list is not in the allowed set.
    """
    labels: List[str]

    @classmethod
    @field_validator("labels", mode="before")
    def check_labels(cls, v: List[str]) -> List[str]:
        """Validates channel labels against allowed values.

        Args:
            v: Input list of labels to validate

        Returns:
            Validated list of labels

        Raises:
            ValueError: When encountering any non-allowed label
        """
        allowed_labels = ("verified",)
        for label in v:
            if label not in allowed_labels:
                raise ValueError(
                    f"Invalid label '{label}', only {allowed_labels} are allowed."
                )
        return v


class Channel(BaseModel):
    """Comprehensive representation of a Telegram channel's profile information.

    Attributes:
        username: Unique @username identifier of the channel
        title: Channel display name with original HTML and parsed text
        description: Channel bio/description with HTML and parsed text (optional)
        avatar: URL of the channel's profile picture (optional)
        counters: Statistics about channel content and subscribers
        labels: Special verification labels (optional)

    Notes:
        Empty avatar URLs are automatically converted to None
    """
    username: str
    title: ParsedAndRaw
    description: Optional[ParsedAndRaw]
    avatar: Optional[HttpUrl] = None
    counters: Counter
    labels: Optional[List[str]] = None

    @field_validator('avatar', mode='before')
    def convert_empty_string_to_none(cls, v: Optional[str]) -> Optional[str]:  # pylint: disable=C0116, E0213
        """Converts empty avatar URLs to None values."""
        if v == "":
            return None
        return v

    @field_validator('labels', mode='before')
    def validate_labels(cls, v: Optional[List[str]]) -> Optional[List[str]]:  # pylint: disable=C0116, E0213
        """Ensures labels only contain allowed values or None."""
        if not v:
            return None

        allowed_labels = ("verified",)
        for label in v:
            if label not in allowed_labels:
                raise ValueError(
                    f"Invalid label '{label}', only {allowed_labels} are allowed."
                )
        return v


class Counter(BaseModel):
    """Container for various channel statistics and metrics.

    Attributes:
        subscribers: Total subscriber count as formatted string
        photos: Count of photo posts (optional)
        videos: Count of video posts (optional)
        files: Count of file attachments (optional)
        links: Count of shared links (optional)
    """
    subscribers: str
    photos: Optional[str] = None
    videos: Optional[str] = None
    files: Optional[str] = None
    links: Optional[str] = None

    @model_validator(mode='before')
    def rewrite_subscriber_key(cls, values):  # pylint: disable=C0116, E0213
        """Normalizes subscriber count field name from 'subscriber' to 'subscribers'."""
        if 'subscriber' in values:
            values['subscribers'] = values.pop('subscriber')
        return values


class Content(BaseModel):
    """Container for a channel's post content.

    Attributes:
        posts: Either a single Post object or list of Post objects
               representing the channel's content
    """
    posts: List[Post] | Post


class ChannelBody(BaseModel):
    """Complete channel representation including profile, content and metadata.

    Attributes:
        channel: Detailed channel profile information
        content: The channel's posts/content
        meta: Pagination and API metadata
    """
    channel: Channel
    content: Content
    meta: Meta
