import re
from typing import Optional, Literal

from pydantic import BaseModel, Field, field_validator

MAX_POST_ID = 10**7
MAX_CHANNELS_IN_PREVIEW = 10
CHANNEL_REGEX = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{3,31}$")


class BaseRequest(BaseModel):
    channel: str = Field(min_length=4, max_length=32)

    @field_validator("channel")
    def validate_channel(cls, v: str) -> str:
        if not re.match(CHANNEL_REGEX, v):
            raise ValueError("Invalid Telegram username format")
        return v


class BaseRequestWithId(BaseRequest):
    identifier: int

    @field_validator("identifier")
    def validate_identifier(cls, v: int) -> int:
        if not str(v).isdigit():
            raise ValueError("Post ID must be a positive integer")
        if v > MAX_POST_ID:
            raise ValueError(f"Post ID must be a positive integer up to {MAX_POST_ID}")
        return v


class BaseRequestWithPosition(BaseRequest):
    position: Optional[int] = Field(None, gt=0, le=MAX_POST_ID)


class BaseRequestWithDirection(BaseRequest):
    direction: Literal["after", "before"]
    position: int = Field(gt=0, le=MAX_POST_ID)


class BaseRequestWithChannels(BaseModel):
    channels: list[str] = Field(min_length=1, max_length=MAX_CHANNELS_IN_PREVIEW)

    @field_validator('channels')
    def validate_channels(cls, v: list[str]) -> list[str]:
        for channel in v:
            if not re.match(CHANNEL_REGEX, channel):
                raise ValueError("Invalid Telegram username format")
        return v
