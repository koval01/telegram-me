"""
Route handler for /body/{channel}
"""

from typing import Optional, Literal, Union
from pydantic import PositiveInt

from fastapi import APIRouter, Path, Query, HTTPException
from model import HTTPError

from telegram.models.body import ChannelBody
from telegram.telegram import Telegram

router = APIRouter()


@router.get(
    "/body/{channel}",
    summary="Get basic information about the channel",
    responses={
        200: {"model": ChannelBody},
        404: {"model": HTTPError}
    }
)
async def body(
        channel: str = Path(description="Telegram channel username."),
        position: Optional[Union[Literal[-1], PositiveInt]] = Query(
            None, description="History position")
) -> dict | None:
    """Request handler"""
    result = await Telegram().body(channel, position)
    if not result:
        raise HTTPException(status_code=404, detail="Channel not found")

    return result
