"""
Route handler for /healthz
"""

from fastapi import APIRouter, Response

router = APIRouter()


@router.get(
    "/healthz",
    summary="Health status",
    responses={200: {}}
)
async def healthz() -> Response:
    """Request handler"""
    return Response(None)
