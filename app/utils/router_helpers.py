from typing import Type, Callable, Awaitable, TypeVar, Dict, Any
from fastapi import HTTPException
from pydantic import BaseModel

from app.telegram.exceptions import (
    TelegramNotFoundError,
    TelegramParseError,
    TelegramUpstreamError,
    TelegramValidationError,
)

T = TypeVar('T', bound=BaseModel)


async def handle_telegram_request(
        telegram_method: Callable[..., Awaitable[Dict[str, Any]]],
        response_model: Type[T],
        *args, **kwargs
) -> Dict:
    """
    Generic handler for Telegram API requests

    Args:
        telegram_method: Async method from Telegram class to call
        response_model: Pydantic model to validate response with
        *args, **kwargs: Arguments to pass to telegram_method

    Returns:
        Validated and formatted response data

    Raises:
        HTTPException: If telegram_method returns None or validation fails
    """
    try:
        result = await telegram_method(*args, **kwargs)
    except TelegramValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TelegramNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TelegramParseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except TelegramUpstreamError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        model_data = response_model.model_validate(result)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=str(e)
        ) from e

    return model_data.model_dump(exclude_none=True, exclude_unset=True)
