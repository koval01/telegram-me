"""
Model for channel preview data
"""

from typing import Optional

from pydantic import BaseModel, HttpUrl


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
    description: Optional[str]
    avatar: Optional[HttpUrl]
    is_verified: bool


class Preview(BaseModel):
    """
    Represents a preview of a Telegram channel.

    Attributes:
        channel (Channel): The channel information.
    """
    channel: Channel
