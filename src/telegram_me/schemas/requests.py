"""Request bodies and small service responses."""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from telegram_me.validation import MAX_CHANNELS_PER_REQUEST, validate_channel_or_raise


class ChannelsRequest(BaseModel):
    """A list of channel usernames."""

    channels: list[str] = Field(min_length=1, max_length=MAX_CHANNELS_PER_REQUEST)

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, value: list[str]) -> list[str]:
        return [validate_channel_or_raise(channel) for channel in value]


class FeedRequest(ChannelsRequest):
    """Channels to build the feed from."""


class FeedResponse(BaseModel):
    """Feed posts ordered by score, each with its `channel` and `_score`."""

    result: list[dict[str, Any]]


class HTTPError(BaseModel):
    """Error response."""

    detail: str


class HealthzResponse(BaseModel):
    """Health endpoint response payload."""

    status: str
