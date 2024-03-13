from typing import Optional

from fastapi import FastAPI, Path, Query, HTTPException
from model import HTTPError

from telegram.models.body import ChannelBody
from telegram.telegram import Telegram

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    redoc_url=None,
    docs_url="/")


@app.get(
    "/body/{channel}",
    summary="Get basic information about the channel",
    responses={
        200: {"model": ChannelBody},
        404: {"model": HTTPError}
    }
)
async def body(
        channel: str = Path(description="Telegram channel username."),
        position: Optional[int] = Query(None, description="History position")
):
    result = await Telegram().body(channel, position)
    if not result:
        raise HTTPException(status_code=404, detail="Channel not found")

    return result
