"""
Route handler for /more/{channel}/{direction}/{position}
"""

from typing import Union, Literal

from fastapi import Path, HTTPException
from pydantic import PositiveInt

from model import HTTPError
from routes.v1.router import router
from telegram.models.more import More
from telegram.telegram import Telegram


@router.get(
    "/more/{channel}/{direction}/{position}",
    summary="Get more posts from the channel",
    responses={
        200: {"model": More},
        404: {"model": HTTPError}
    },
    tags=["Channel"]
)
async def more(
        channel: str = Path(description="Telegram channel username."),
        direction: Literal["after", "before"] = Path(description="History load direction"),
        position: Union[Literal[-1], PositiveInt] = Path(description="History position")
) -> dict | None:
    """Request handler"""
    result = await Telegram().more(channel, position, direction)
    if not result:
        raise HTTPException(status_code=404, detail="Channel or post not found")

    return result
