"""
Route handler for /preview/{channel}
"""

from fastapi import Path, HTTPException, APIRouter

from app.models.error import HTTPError
from app.telegram.models.preview import Preview
from app.telegram.telegram import Telegram

router = APIRouter()


@router.get(
    "/preview/{channel}",
    summary="Get preview information of channel",
    responses={200: {"model": Preview}, 404: {"model": HTTPError}},
    tags=["Channel"],
)
async def preview(
    channel: str = Path(description="Telegram channel username."),
) -> dict | None:
    """Request handler"""
    result = await Telegram().preview(channel)
    if not result:
        raise HTTPException(status_code=404, detail="Channel not found")

    try:
        preview_data = Preview.model_validate(result)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=str(e)
        )

    return preview_data.model_dump(exclude_none=True, exclude_unset=True)
