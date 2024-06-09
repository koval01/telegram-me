"""
Route handler for /preview/{channel}
"""

from fastapi import Path, HTTPException

from model import HTTPError
from routes.v1.router import router
from telegram.models.preview import Preview
from telegram.telegram import Telegram


@router.get(
    "/preview/{channel}",
    summary="Get preview information of channel",
    responses={
        200: {"model": Preview},
        404: {"model": HTTPError}
    },
    tags=["Channel"]
)
async def preview(
        channel: str = Path(description="Telegram channel username.")
) -> dict | None:
    """Request handler"""
    result = await Telegram().preview(channel)
    if not result:
        raise HTTPException(status_code=404, detail="Channel not found")

    return result
