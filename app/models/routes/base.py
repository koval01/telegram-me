import re
from typing import Optional, Literal

from pydantic import BaseModel, Field, field_validator

MAX_POST_ID = 10 ** 7
MAX_CHANNELS_IN_PREVIEW = 100
CHANNEL_REGEX = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{3,31}$")


class BaseRequest(BaseModel):
    """Base request model containing channel validation."""

    channel: str = Field(min_length=4, max_length=32)

    @field_validator("channel")
    def validate_channel(cls, v: str) -> str:  # pylint: disable=C0116, E0213
        """Validate that the channel name matches Telegram username format.

        Args:
            v: Channel name to validate

        Returns:
            Validated channel name

        Raises:
            ValueError: If channel name doesn't match the required format
        """
        if not re.match(CHANNEL_REGEX, v):
            raise ValueError("Invalid Telegram username format")
        return v


class BaseRequestWithId(BaseRequest):
    """Base request model that includes a post identifier."""

    identifier: int

    @field_validator("identifier")
    def validate_identifier(cls, v: int) -> int:  # pylint: disable=C0116, E0213
        """Validate that the identifier is a valid post ID.

        Args:
            v: Post ID to validate

        Returns:
            Validated post ID

        Raises:
            ValueError: If post ID is not a positive integer within allowed range
        """
        if not str(v).isdigit():
            raise ValueError("Post ID must be a positive integer")
        if v > MAX_POST_ID:
            raise ValueError(f"Post ID must be a positive integer up to {MAX_POST_ID}")
        return v


class BaseRequestWithPosition(BaseRequest):
    """Base request model that includes an optional position parameter."""

    position: Optional[int] = Field(None, gt=0, le=MAX_POST_ID)


class BaseRequestWithDirection(BaseRequest):
    """Base request model that includes direction and position parameters."""

    direction: Literal["after", "before"]
    position: int = Field(gt=0, le=MAX_POST_ID)


class BaseRequestWithChannels(BaseModel):
    """Base request model that handles multiple channels."""

    channels: list[str] = Field(min_length=1, max_length=MAX_CHANNELS_IN_PREVIEW)

    @field_validator('channels')
    def validate_channels(cls, v: list[str]) -> list[str]:  # pylint: disable=C0116, E0213
        """Validate that all channel names match Telegram username format.

        Args:
            v: List of channel names to validate

        Returns:
            List of validated channel names

        Raises:
            ValueError: If any channel name doesn't match the required format
        """
        for channel in v:
            if not re.match(CHANNEL_REGEX, channel):
                raise ValueError("Invalid Telegram username format")
        return v
