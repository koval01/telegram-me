import os

from fastapi import Request, HTTPException, status
from typing import Optional, Callable

from app.services.recaptcha import Recaptcha


class RecaptchaMiddleware:
    """
    Middleware for ReCAPTCHA validation in FastAPI routes.

    Usage:
    router = APIRouter(dependencies=[Depends(RecaptchaMiddleware())])
    """

    def __init__(
            self,
            header_name: str = "X-Recaptcha-Token",
            remote_ip_extractor: Optional[Callable[[Request], str]] = None,
    ):
        self.header_name = header_name
        self.remote_ip_extractor = remote_ip_extractor
        self.recaptcha = Recaptcha()

    async def __call__(self, request: Request) -> None:
        if os.getenv("ENVIRONMENT") == "development":
            return None

        token = request.headers.get(self.header_name)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ReCAPTCHA token missing"
            )

        remote_ip = None
        if self.remote_ip_extractor:
            remote_ip = self.remote_ip_extractor(request)

        try:
            response = await self.recaptcha.validate(token, remote_ip)
            if not response.success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid ReCAPTCHA token"
                )
            return None
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"ReCAPTCHA validation failed: {str(e)}"
            )
