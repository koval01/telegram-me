"""
Model for channel preview data
"""

from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator


class Channel(BaseModel):
    """
    Represents a Telegram channel.

    Attributes:
        title (str): The title of the channel.
        subscribers (str): The number of subscribers to the channel.
        description (Optional[str]): The description of the channel.
        avatar (Optional[HttpUrl]): Avatar photo of channel.
        is_verified (bool): Indicates if the channel is verified.
    """

    title: str
    subscribers: str
    description: Optional[str] = None
    avatar: Optional[HttpUrl] = None
    is_verified: bool

    @field_validator('avatar', mode='before')
    def convert_empty_list_to_none(cls, v: object) -> Optional[object]:
        if isinstance(v, str) and v.startswith("data:"):
            return None
        return v


class Preview(BaseModel):
    """
    Represents a preview of a Telegram channel.

    Attributes:
        channel (Channel): The channel information.
    """

    channel: Channel
