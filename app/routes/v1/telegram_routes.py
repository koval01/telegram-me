"""
Consolidated route handlers for Telegram API endpoints
"""

from typing import Optional, Dict, Any, Literal, Annotated
from pydantic import PositiveInt, AfterValidator

from fastapi import APIRouter, Query
from app.models.error import HTTPError

from app.telegram.models.body import ChannelBody
from app.telegram.models.more import More
from app.telegram.models.post import Post
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
from app.utils.validators import PydanticValidator

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
    responses={200: {"model": Post}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def post(
    channel: str = channel_param(),
    identifier: PositiveInt = identifier_param(),
) -> Dict[str, Any]:
    """Request handler for getting a specific post"""
    return await handle_telegram_request(
        telegram.post, Post, channel, identifier
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


@router.get(
    "/previews",
    summary="Get preview data about the channel group",
    responses={200: {"model": Previews}, 400: {"model": HTTPError}},
)
async def previews(  # pylint: disable=W0102
    channels: Annotated[list[str], Query(
        ...,
        description="List of channel identifiers to fetch previews for",
        alias="q",
        min_length=1,
        max_length=10,
    ), AfterValidator(PydanticValidator.check_valid_usernames)
    ] = ["telegram"]
) -> Dict[str, Any]:
    """Request handler for retrieving preview data for multiple channels"""
    return await handle_telegram_request(
        telegram.previews, Previews, channels
    )
