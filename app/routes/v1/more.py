"""
Route handler for /more/{channel}/{direction}/{position}
"""

from typing import Literal

from fastapi import Path, HTTPException, APIRouter
from pydantic import PositiveInt

from app.models.error import HTTPError
from app.telegram.models.more import More
from app.telegram.telegram import Telegram

router = APIRouter()

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
        position: PositiveInt = Path(description="History position")
) -> dict | None:
    """Request handler"""
    result = await Telegram().more(channel, position, direction)

    if not result:
        raise HTTPException(status_code=404, detail="Channel not found")
    if not result.get("posts"):
        return result

    return result
