"""
Route handler for /post/{channel}/{identifier}
"""

from fastapi import Path, HTTPException, APIRouter
from pydantic import PositiveInt

from app.models.error import HTTPError
from app.telegram.models.body import ChannelBody
from app.telegram.telegram import Telegram

router = APIRouter()


@router.get(
    "/post/{channel}/{identifier}",
    summary="Get one post from the channel",
    responses={200: {"model": ChannelBody}, 404: {"model": HTTPError}},
    tags=["Channel"],
)
async def post(
    channel: str = Path(description="Telegram channel username."),
    identifier: PositiveInt = Path(description="Post identifier"),
) -> dict | None:
    """Request handler"""
    result = await Telegram().post(channel, identifier)
    if not result:
        raise HTTPException(
            status_code=404, detail="Channel or post not found"
        )

    return result
