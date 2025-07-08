from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator


class Channel(BaseModel):
    """Represents a Telegram channel with its metadata and statistics.

    Attributes:
        title (str): Official name of the channel as displayed in Telegram.
        subscribers (str): Formatted string representing subscriber count
            (e.g., "1.2M" or "45,678").
        description (Optional[str]): Channel's about/bio text. May include
            markdown formatting and links (None if not set).
        avatar (Optional[HttpUrl]): URL of the channel's profile picture.
            Returns None if no avatar is set or if the URL contains embedded
            image data (data URI scheme).
        is_verified (bool): Indicates whether the channel has official
            verification status (blue checkmark).

    Notes:
        The avatar validator automatically converts data URIs to None to
        handle cases where Telegram returns embedded image data instead of
        a proper URL.
    """

    title: str
    subscribers: str
    description: Optional[str] = None
    avatar: Optional[HttpUrl] = None
    is_verified: bool

    @field_validator('avatar', mode='before')
    def convert_empty_list_to_none(cls, v: object) -> Optional[object]:  # pylint: disable=C0116, E0213
        """Converts data URI avatars to None to handle embedded image cases.

        Args:
            v: Raw input value that may be a string URL or data URI

        Returns:
            Original URL if valid, None if input is a data URI
        """
        if isinstance(v, str) and v.startswith("data:"):
            return None
        return v


class Preview(BaseModel):
    """Container for channel information in preview contexts.

    Attributes:
        channel (Optional[Channel]): Complete channel data when available.
            None indicates the channel couldn't be fetched or doesn't exist.

    Usage:
        Used in contexts where channel information might be partial or
        unavailable, such as:
        - Link previews
        - Deleted channel references
        - Rate-limited API responses
    """

    channel: Optional[Channel] = None
