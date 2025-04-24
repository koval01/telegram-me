from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Union

import aiohttp
from pydantic import BaseModel, HttpUrl, ValidationError, field_validator
from aiohttp import ClientTimeout

from app.utils.config import settings


class RecaptchaErrorCode(str, Enum):
    """Enumeration of possible reCAPTCHA error codes."""
    MISSING_INPUT_SECRET = "missing-input-secret"
    INVALID_INPUT_SECRET = "invalid-input-secret"
    MISSING_INPUT_RESPONSE = "missing-input-response"
    INVALID_INPUT_RESPONSE = "invalid-input-response"
    BAD_REQUEST = "bad-request"
    TIMEOUT_OR_DUPLICATE = "timeout-or-duplicate"


class RecaptchaResponse(BaseModel):
    """Model for reCAPTCHA API response."""
    success: bool
    challenge_ts: Optional[datetime] = None
    hostname: Optional[str] = None
    apk_package_name: Optional[str] = None
    error_codes: Optional[List[RecaptchaErrorCode]] = None

    @field_validator('challenge_ts', mode='before')
    def parse_timestamp(cls, value: Optional[str]) -> Optional[datetime]:
        if value is None:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        except (ValueError, TypeError):
            return None


class Recaptcha:
    """
    A class for validating reCAPTCHA tokens using Google's API.

    Args:
        api_url: The reCAPTCHA verification API URL (defaults to Google's)
        timeout: Timeout in seconds for the API request
        session: Optional aiohttp client session to reuse
    """

    DEFAULT_API_URL = "https://www.google.com/recaptcha/api/siteverify"

    def __init__(
            self,
            api_url: Union[str, HttpUrl] = DEFAULT_API_URL,
            timeout: float = 10.0,
            session: Optional[aiohttp.ClientSession] = None
    ):
        self.secret_key = settings.GOOGLE_RECAPTCHA_SECRET
        self.api_url = api_url
        self.timeout = timeout
        self._session = session
        self._own_session = session is None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self) -> None:
        """Close the client session if it was created by this instance."""
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def validate(
            self,
            token: str,
            remote_ip: Optional[str] = None
    ) -> RecaptchaResponse:
        """
        Validate a reCAPTCHA token with Google's API.

        Args:
            token: The reCAPTCHA response token to validate
            remote_ip: Optional IP address of the user

        Returns:
            RecaptchaResponse: The validation response

        Raises:
            ValueError: If the token is empty or invalid
            aiohttp.ClientError: If there's an HTTP error during validation
            ValidationError: If the API response is malformed
        """
        if not token or not isinstance(token, str):
            raise ValueError("Token must be a non-empty string")

        payload: Dict[str, str] = {
            "secret": self.secret_key,
            "response": token,
        }

        if remote_ip is not None:
            payload["remoteip"] = remote_ip

        session = await self._get_session()
        try:
            async with session.post(
                url=str(self.api_url),
                data=payload,
                timeout=ClientTimeout(total=self.timeout)
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return RecaptchaResponse.model_validate(data)
        except aiohttp.ClientError as e:
            raise aiohttp.ClientError(f"reCAPTCHA validation failed: {str(e)}") from e
        except ValidationError as e:
            raise ValidationError(
                f"Invalid reCAPTCHA response format: {str(e)}"
            ) from e

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp client session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session
