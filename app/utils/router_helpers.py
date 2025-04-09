"""
API router helpers
"""
from typing import Type, Callable, Awaitable, TypeVar, Dict, Optional
from fastapi import HTTPException
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


async def handle_telegram_request(
        telegram_method: Callable[..., Awaitable[Optional[Dict]]],
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
    result = await telegram_method(*args, **kwargs)

    if not result:
        raise HTTPException(
            status_code=404, detail="Requested resource not found"
        )

    try:
        model_data = response_model.model_validate(result)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=str(e)
        ) from e

    return model_data.model_dump(exclude_none=True, exclude_unset=True)
