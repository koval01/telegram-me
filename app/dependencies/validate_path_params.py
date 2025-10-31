from typing import Optional, Literal

from fastapi import Path, Query, Body, HTTPException
from starlette import status

from app.models.routes.base import (
    BaseRequest,
    BaseRequestWithId,
    BaseRequestWithPosition,
    BaseRequestWithDirection,
    BaseRequestWithChannels,
    MAX_POST_ID, CHANNEL_REGEX
)


async def validate_channel(
    channel: str = Path(..., pattern=CHANNEL_REGEX,
        description="Telegram channel username"),
) -> BaseRequest:
    """
    Validate and return a base request with a Telegram channel.

    Args:
        channel: Telegram channel username (must match CHANNEL_REGEX pattern)

    Returns:
        BaseRequest: A base request object containing the validated channel

    Raises:
        HTTPException: If channel validation fails (handled by FastAPI)
    """
    return BaseRequest(channel=channel)


async def validate_channel_and_id(
    channel: str = Path(..., pattern=CHANNEL_REGEX,
                        description="Telegram channel username"),
    identifier: int = Path(..., gt=0, le=MAX_POST_ID, description="Post ID"),
) -> BaseRequestWithId:
    """
    Validate and return a request with channel- and post-ID.

    Args:
        channel: Telegram channel username (must match CHANNEL_REGEX pattern)
        identifier: Post ID (must be greater than 0 and less than or equal to MAX_POST_ID)

    Returns:
        BaseRequestWithId: A request object containing channel- and post-ID

    Raises:
        HTTPException: If either parameter validation fails (handled by FastAPI)
    """
    return BaseRequestWithId(channel=channel, identifier=identifier)


async def get_channel_body_params(
    channel: str = Path(..., pattern=CHANNEL_REGEX),
    position: Optional[int] = Query(None, gt=0, le=MAX_POST_ID)
) -> BaseRequestWithPosition:
    """
    Validate and return a request with a channel and optional position.

    Args:
        channel: Telegram channel username (must match CHANNEL_REGEX pattern)
        position: Optional post position (must be greater than 0 and
                 less than or equal to MAX_POST_ID if provided)

    Returns:
        BaseRequestWithPosition: A request object containing a channel and optional position

    Raises:
        HTTPException: If parameter validation fails (handled by FastAPI)
    """
    return BaseRequestWithPosition(channel=channel, position=position)


async def validate_more_params(
    channel: str = Path(..., pattern=CHANNEL_REGEX),
    direction: Literal["after", "before"] = Path(...),
    position: int = Path(..., gt=0, le=MAX_POST_ID)
) -> BaseRequestWithDirection:
    """
    Validate and return a request with a channel, direction, and position.

    Args:
        channel: Telegram channel username (must match CHANNEL_REGEX pattern)
        direction: Scroll direction, either "after" or "before"
        position: Post position (must be greater than 0 and less than or equal to MAX_POST_ID)

    Returns:
        BaseRequestWithDirection: A request object containing a channel, direction and position

    Raises:
        HTTPException: If parameter validation fails (handled by FastAPI)
    """
    return BaseRequestWithDirection(
        channel=channel,
        direction=direction,
        position=position
    )


async def validate_previews_params(
    channels: list[str] = Body(
        ["telegram"],
        description="List of channel identifiers",
        min_length=1,
        max_length=100
    )
) -> BaseRequestWithChannels:
    """
    Validate and return a request with multiple channels.

    Args:
        channels: List of channel identifiers (default: ["telegram"])
                 Must contain 1-100 items when provided

    Returns:
        BaseRequestWithChannels: A request object containing the list of channels

    Raises:
        HTTPException: 422 status if channel validation fails
    """
    try:
        return BaseRequestWithChannels(channels=channels)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        ) from e
