"""Mapping of domain errors to HTTP responses."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from telegram_me.telegram.errors import (
    OverloadedError,
    TelegramError,
    TelegramNotFoundError,
    TelegramParseError,
    TelegramUpstreamError,
    TelegramValidationError,
)

logger = logging.getLogger(__name__)


class FeatureDisabledError(Exception):
    """The endpoint is switched off by server configuration."""

    def __init__(self, feature: str) -> None:
        super().__init__(f"Feature '{feature}' is disabled by server configuration")


_STATUS_BY_ERROR: tuple[tuple[type[TelegramError], int], ...] = (
    (TelegramValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (TelegramNotFoundError, status.HTTP_404_NOT_FOUND),
    (TelegramParseError, status.HTTP_502_BAD_GATEWAY),
    (TelegramUpstreamError, status.HTTP_503_SERVICE_UNAVAILABLE),
)


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code)


async def _telegram_error(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, OverloadedError):
        # Shed load: tell clients (and Cloudflare) when to come back.
        response = _error(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
        response.headers["Retry-After"] = str(exc.retry_after)
        return response
    for error_type, status_code in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            if status_code >= 500:
                logger.warning("%s: %s", type(exc).__name__, exc, exc_info=exc.__cause__)
            return _error(status_code, str(exc))
    logger.error("Unhandled Telegram error", exc_info=exc)
    return _error(status.HTTP_502_BAD_GATEWAY, str(exc))


async def _feature_disabled(_request: Request, exc: Exception) -> JSONResponse:
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers on `app`."""
    app.add_exception_handler(TelegramError, _telegram_error)
    app.add_exception_handler(FeatureDisabledError, _feature_disabled)
