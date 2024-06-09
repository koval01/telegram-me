"""
Route handler for /previews
"""

from typing import AnyStr, Any, List, Union, Dict

from fastapi import HTTPException

from model import HTTPError
from routes.v1.router import router
from telegram.models.previews import Previews
from telegram.telegram import Telegram

JSONObject = Dict[AnyStr, Any]
JSONArray = List[Any]
JSONStructure = Union[JSONArray, JSONObject]


@router.post(
    "/previews",
    summary="Get preview data about the channel group",
    responses={
        200: {"model": Previews},
        400: {"model": HTTPError}
    },
    tags=["Channel"]
)
async def previews(payload: JSONStructure = None) -> dict | None:
    """Request handler"""
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=400, detail="The input value can only be a list")
    if not 0 < len(payload) <= 10:
        raise HTTPException(
            status_code=400, detail="There must be between 1 and 10 elements in the list")
    for item in payload:
        if not isinstance(item, str):
            raise HTTPException(
                status_code=400, detail="All elements in the list must be strings")
        if len(item) == 0:
            raise HTTPException(
                status_code=400, detail="Strings cannot be empty")
        if len(item) > 32:
            raise HTTPException(
                status_code=400, detail="Strings cannot be longer than 32 characters")

    return {channel: await Telegram().preview(channel) for channel in payload}
