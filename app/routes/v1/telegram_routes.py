from fastapi import APIRouter, Depends
from typing import Dict, Any

from app.dependencies.validate_path_params import (
    validate_channel,
    validate_channel_and_id,
    get_channel_body_params,
    validate_more_params,
    validate_previews_params
)
from app.models.error import HTTPError
from app.models.routes.base import (
    BaseRequestWithChannels,
    BaseRequest,
    BaseRequestWithId,
    BaseRequestWithDirection,
    BaseRequestWithPosition
)
from app.telegram.models import ChannelBody, More, Post, Preview, Previews
from app.telegram.telegram import Telegram
from app.utils.router_helpers import handle_telegram_request

router = APIRouter(tags=["Channel"])
telegram = Telegram()


@router.get(
    "/body/{channel}",
    summary="Get basic information about the channel",
    responses={200: {"model": ChannelBody}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def body(
    params: BaseRequestWithPosition = Depends(get_channel_body_params),
) -> Dict[str, Any]:
    return await handle_telegram_request(
        telegram.body, ChannelBody, params.channel, params.position
    )


@router.get(
    "/more/{channel}/{direction}/{position}",
    summary="Get more posts from the channel",
    responses={200: {"model": More}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def more(
    params: BaseRequestWithDirection = Depends(validate_more_params),
) -> Dict[str, Any]:
    return await handle_telegram_request(
        telegram.more, More, params.channel, params.position, params.direction
    )


@router.get(
    "/post/{channel}/{identifier}",
    summary="Get one post from the channel",
    responses={200: {"model": Post}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def post(
    params: BaseRequestWithId = Depends(validate_channel_and_id),
) -> Dict[str, Any]:
    return await handle_telegram_request(
        telegram.post, Post, params.channel, params.identifier
    )


@router.get(
    "/preview/{channel}",
    summary="Get preview information of channel",
    responses={200: {"model": Preview}, 404: {"model": HTTPError}, 400: {"model": HTTPError}},
)
async def preview(
    params: BaseRequest = Depends(validate_channel),
) -> Dict[str, Any]:
    return await handle_telegram_request(
        telegram.preview, Preview, params.channel
    )


@router.get(
    "/previews",
    summary="Get preview data about the channel group",
    responses={200: {"model": Previews}, 400: {"model": HTTPError}},
)
async def previews(
    params: BaseRequestWithChannels = Depends(validate_previews_params),
) -> Dict[str, Any]:
    return await handle_telegram_request(
        telegram.previews, Previews, params.channels
    )
