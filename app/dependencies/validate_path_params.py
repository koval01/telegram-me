from fastapi import Path, Query, Depends, HTTPException
from typing import Optional, Literal

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
    return BaseRequest(channel=channel)


async def validate_channel_and_id(
    channel: str = Path(..., pattern=CHANNEL_REGEX,
                        description="Telegram channel username"),
    identifier: int = Path(..., gt=0, le=MAX_POST_ID, description="Post ID"),
) -> BaseRequestWithId:
    return BaseRequestWithId(channel=channel, identifier=identifier)


async def get_channel_body_params(
    channel: str = Path(..., pattern=CHANNEL_REGEX),
    position: Optional[int] = Query(None, gt=0, le=MAX_POST_ID)
) -> BaseRequestWithPosition:
    return BaseRequestWithPosition(channel=channel, position=position)


async def validate_more_params(
    channel: str = Path(..., pattern=CHANNEL_REGEX),
    direction: Literal["after", "before"] = Path(...),
    position: int = Path(..., gt=0, le=MAX_POST_ID)
) -> BaseRequestWithDirection:
    return BaseRequestWithDirection(
        channel=channel,
        direction=direction,
        position=position
    )


async def validate_previews_params(
    channels: list[str] = Query(
        ["telegram"],
        description="List of channel identifiers",
        alias="q",
        min_length=1,
        max_length=10
    )
) -> BaseRequestWithChannels:
    try:
        return BaseRequestWithChannels(channels=channels)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
