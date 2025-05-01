import os
from typing import Optional, Callable

from fastapi import Request, HTTPException, status

from app.services.recaptcha import Recaptcha


class RecaptchaMiddleware:
    """
    Middleware for ReCAPTCHA validation in FastAPI routes.

    Usage:
    router = APIRouter(dependencies=[Depends(RecaptchaMiddleware())])

    Args:
        header_name: Name of the header containing the ReCAPTCHA token
        remote_ip_extractor: Optional function to extract remote IP from request
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
        """Validate the ReCAPTCHA token in the request."""
        if self._is_development():
            return None

        token = self._get_token_from_header(request)
        remote_ip = self._extract_remote_ip(request)
        await self._validate_recaptcha(token, remote_ip)
        return None

    def _is_development(self) -> bool:
        """Check if the environment is development."""
        return os.getenv("ENVIRONMENT") == "development"

    def _get_token_from_header(self, request: Request) -> str:
        """Extract and validate the token from the header."""
        token = request.headers.get(self.header_name)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ReCAPTCHA token missing"
            )
        return token

    def _extract_remote_ip(self, request: Request) -> Optional[str]:
        """Extract remote IP from request if extractor is provided."""
        if self.remote_ip_extractor:
            return self.remote_ip_extractor(request)
        return None

    async def _validate_recaptcha(self, token: str, remote_ip: Optional[str]) -> None:
        """Validate the ReCAPTCHA token."""
        try:
            response = await self.recaptcha.validate(token, remote_ip)
            if not response.success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid ReCAPTCHA token"
                )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"ReCAPTCHA validation failed: {str(e)}"
            ) from e
