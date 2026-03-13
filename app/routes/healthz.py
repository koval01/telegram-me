from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthzResponse(BaseModel):
    """Health endpoint response payload."""

    status: str


@router.get(
    "/healthz",
    summary="Health status",
    response_model=HealthzResponse,
    responses={200: {"model": HealthzResponse}},
    tags=["Service"],
)
async def healthz() -> HealthzResponse:
    """Request handler"""
    return HealthzResponse(status="ok")
