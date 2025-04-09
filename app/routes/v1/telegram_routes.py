"""
Consolidated route handlers for Telegram API endpoints
"""

from typing import Optional, Dict, Any, List, Literal
from pydantic import PositiveInt

from fastapi import APIRouter, HTTPException
from app.models.error import HTTPError

from app.telegram.models.body import ChannelBody
from app.telegram.models.more import More
from app.telegram.models.preview import Preview
from app.telegram.models.previews import Previews
from app.telegram.telegram import Telegram
from app.utils.parameters import (
    channel_param,
    position_path_param,
    position_query_param,
    direction_param,
    identifier_param
)
from app.utils.router_helpers import handle_telegram_request

router = APIRouter(tags=["Channel"])

# Create a single Telegram instance to be used across all route handlers
telegram = Telegram()


@router.get(
    "/body/{channel}",
    summary="Get basic information about the channel",
    responses={200: {"model": ChannelBody}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def body(
    channel: str = channel_param(),
    position: Optional[PositiveInt] = position_query_param(),
) -> Dict[str, Any]:
    """Request handler for channel body information"""
    return await handle_telegram_request(
        telegram.body, ChannelBody, channel, position
    )


@router.get(
    "/more/{channel}/{direction}/{position}",
    summary="Get more posts from the channel",
    responses={200: {"model": More}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def more(
    channel: str = channel_param(),
    direction: Literal["after", "before"] = direction_param(),
    position: PositiveInt = position_path_param(),
) -> Dict[str, Any]:
    """Request handler for loading more posts"""
    return await handle_telegram_request(
        telegram.more, More, channel, position, direction
    )


@router.get(
    "/post/{channel}/{identifier}",
    summary="Get one post from the channel",
    responses={200: {"model": ChannelBody}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def post(
    channel: str = channel_param(),
    identifier: PositiveInt = identifier_param(),
) -> Dict[str, Any]:
    """Request handler for getting a specific post"""
    return await handle_telegram_request(
        telegram.post, ChannelBody, channel, identifier
    )


@router.get(
    "/preview/{channel}",
    summary="Get preview information of channel",
    responses={200: {"model": Preview}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def preview(
    channel: str = channel_param(),
) -> Dict[str, Any]:
    """Request handler for channel preview"""
    return await handle_telegram_request(
        telegram.preview, Preview, channel
    )


# Helper functions for previews endpoint
def _validate_channel_type(channels: Any) -> None:
    """Validate that input is a list"""
    if not isinstance(channels, list):
        raise HTTPException(
            status_code=400, detail="The input value can only be a list"
        )


def _validate_channel_count(channels: List) -> None:
    """Validate the number of channels"""
    if not 0 < len(channels) <= 10:
        raise HTTPException(
            status_code=400,
            detail="There must be between 1 and 10 elements in the list",
        )


def _validate_channel_content(channel: Any) -> None:
    """Validate individual channel content"""
    if not isinstance(channel, str):
        raise HTTPException(
            status_code=400, detail="All elements in the list must be strings"
        )

    if not channel:
        raise HTTPException(status_code=400, detail="Strings cannot be empty")

    if len(channel) > 32:
        raise HTTPException(
            status_code=400,
            detail="Strings cannot be longer than 32 characters",
        )


async def validate_channels(channels: Any) -> List[str]:
    """Validate input payload containing channel identifiers"""
    _validate_channel_type(channels)
    _validate_channel_count(channels)

    for channel in channels:
        _validate_channel_content(channel)

    return channels


async def fetch_previews(channels: List[str]) -> Dict[str, Any]:
    """Fetch preview data for each channel"""
    result = {}
    for channel in channels:
        result[channel] = await telegram.preview(channel)

    try:
        previews_data = Previews.model_validate(result)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=str(e)
        )

    return previews_data.model_dump(exclude_none=True, exclude_unset=True)


@router.post(
    "/previews",
    summary="Get preview data about the channel group",
    responses={200: {"model": Previews}, 400: {"model": HTTPError}},
)
async def previews(payload: List[str] = None) -> Dict[str, Any]:
    """Request handler for retrieving preview data for multiple channels"""
    validated_channels = await validate_channels(payload)
    return await fetch_previews(validated_channels)
