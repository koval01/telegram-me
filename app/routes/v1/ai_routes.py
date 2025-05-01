"""
Consolidated route handlers for work with LLM
"""

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies.gemini import get_gemini_service
from app.middleware.recaptcha import RecaptchaMiddleware

from app.models.error import HTTPError
from app.models.routes.ai_routes import GenerateRequest
from app.services.ai_processor import GenerateResponse

from app.services.gemini import GeminiService

from app.telegram.telegram import Telegram
from app.telegram.models.body import ChannelBody

from app.utils.router_helpers import handle_telegram_request

router = APIRouter(
    tags=["AI"],
    dependencies=[Depends(RecaptchaMiddleware())]
)

telegram = Telegram()


@router.post(
    "/generate",
    summary="Generating neural network thoughts about the post",
    response_model=None,
    responses={
        200: {"model": None},
        404: {"model": HTTPError},
        400: {"model": HTTPError},
    },
)
async def generate(
    request: GenerateRequest,
    gemini_service: GeminiService = Depends(get_gemini_service),
) -> dict:
    """Request handler for LLM response"""
    post = await handle_telegram_request(
        telegram.post, ChannelBody,
        request.channel, request.identifier, False
    )
    ai = GenerateResponse(post, gemini_service, lang=request.lang)
    availability, reason = ai.check_availability()
    if not availability:
        raise HTTPException(status_code=400, detail=reason)
    response = await ai.generate()
    if not response:
        raise HTTPException(status_code=400, detail="AI error")
    return response
