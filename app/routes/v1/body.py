"""
Route handler for /body/{channel}
"""

from typing import Optional
from pydantic import PositiveInt

from fastapi import Path, Query, HTTPException, APIRouter
from app.models.error import HTTPError

from app.telegram.models.body import ChannelBody
from app.telegram.telegram import Telegram

router = APIRouter()


@router.get(
    "/body/{channel}",
    summary="Get basic information about the channel",
    responses={200: {"model": ChannelBody}, 404: {"model": HTTPError}},
    tags=["Channel"],
)
async def body(
    channel: str = Path(description="Telegram channel username."),
    position: Optional[PositiveInt] = Query(
        None, description="History position"
    ),
) -> dict | None:
    """Request handler"""
    result = await Telegram().body(channel, position)
    if not result:
        raise HTTPException(
            status_code=404, detail="Channel or post not found"
        )

    try:
        channel_data = ChannelBody.model_validate(result)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=str(e)
        )

    return channel_data.model_dump(exclude_none=True, exclude_unset=True)
