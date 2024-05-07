"""
Route handler for /healthz
"""

from fastapi import Response, APIRouter

router = APIRouter()


@router.get(
    "/healthz",
    summary="Health status",
    responses={200: {}},
    tags=["Service"]
)
async def healthz() -> Response:
    """Request handler"""
    return Response(None)
