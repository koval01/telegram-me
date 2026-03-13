from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.utils.validation import validate_channel_or_raise


class FeedRequest(BaseModel):
    """Request payload for feed aggregation endpoint."""

    channels: list[str] = Field(min_length=1, max_length=100)

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, value: list[str]) -> list[str]:
        """Validate and normalize all channel names."""
        return [validate_channel_or_raise(channel) for channel in value]


class FeedResponse(BaseModel):
    """Response payload for feed aggregation endpoint."""

    result: list[dict[str, Any]]
