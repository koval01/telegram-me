"""
Route handler for /post/{channel}/{identifier}
"""

from typing import Union, Literal

from fastapi import Path, HTTPException
from pydantic import PositiveInt

from model import HTTPError
from routes.v1.router import router
from telegram.models.body import ChannelBody
from telegram.telegram import Telegram


@router.get(
    "/post/{channel}/{identifier}",
    summary="Get one post from the channel",
    responses={
        200: {"model": ChannelBody},
        404: {"model": HTTPError}
    },
    tags=["Channel"]
)
async def post(
        channel: str = Path(description="Telegram channel username."),
        identifier: Union[Literal[-1], PositiveInt] = Path(description="Post identifier")
) -> dict | None:
    """Request handler"""
    result = await Telegram().post(channel, identifier)
    if not result:
        raise HTTPException(status_code=404, detail="Channel or post not found")

    return result
