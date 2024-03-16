from typing import Union, Literal
from pydantic import PositiveInt

from fastapi import APIRouter, Path, HTTPException
from model import HTTPError

from telegram.models.more import More

from telegram.telegram import Telegram

router = APIRouter()


@router.get(
    "/more/{channel}/{direction}/{position}",
    summary="Get more posts from the channel",
    responses={
        200: {"model": More},
        404: {"model": HTTPError}
    }
)
async def more(
        channel: str = Path(description="Telegram channel username."),
        direction: Literal["after", "before"] = Path(description="History load direction"),
        position: Union[Literal[-1], PositiveInt] = Path(description="History position")
):
    result = await Telegram().more(channel, position, direction)
    if not result:
        raise HTTPException(status_code=404, detail="Channel not found")

    return result
