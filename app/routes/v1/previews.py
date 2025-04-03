"""
Route handler for /previews
"""

from typing import List, Dict, Any

from fastapi import HTTPException, APIRouter

from app.models.error import HTTPError
from app.telegram.models.previews import Previews
from app.telegram.telegram import Telegram

router = APIRouter()


def _validate_channel_type(channels: Any) -> None:
    """Validate that input is a list"""
    if not isinstance(channels, list):
        raise HTTPException(
            status_code=400, detail="The input value can only be a list"
        )


def _validate_channel_count(channels: List) -> None:
    """Validate the number of channels"""
    if not 0 < len(channels) <= 10:
        raise HTTPException(
            status_code=400,
            detail="There must be between 1 and 10 elements in the list",
        )


def _validate_channel_content(channel: Any) -> None:
    """Validate individual channel content"""
    if not isinstance(channel, str):
        raise HTTPException(
            status_code=400, detail="All elements in the list must be strings"
        )

    if not channel:
        raise HTTPException(status_code=400, detail="Strings cannot be empty")

    if len(channel) > 32:
        raise HTTPException(
            status_code=400,
            detail="Strings cannot be longer than 32 characters",
        )


async def validate_channels(channels: Any) -> List[str]:
    """Validate input payload containing channel identifiers"""
    _validate_channel_type(channels)
    _validate_channel_count(channels)

    for channel in channels:
        _validate_channel_content(channel)

    return channels


async def fetch_previews(channels: List[str]) -> Dict[str, Any]:
    """Fetch preview data for each channel"""
    telegram = Telegram()
    result = {}

    for channel in channels:
        result[channel] = await telegram.preview(channel)

    return result


@router.post(
    "/previews",
    summary="Get preview data about the channel group",
    responses={200: {"model": Previews}, 400: {"model": HTTPError}},
    tags=["Channel"],
)
async def previews(payload: List[str] = None) -> Dict[str, Any]:
    """Request handler for retrieving preview data for multiple channels"""
    validated_channels = await validate_channels(payload)
    return await fetch_previews(validated_channels)
