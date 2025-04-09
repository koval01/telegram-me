"""
Responses for API routes
"""

from typing import Type, Dict
from pydantic import BaseModel
from app.models.error import HTTPError


def standard_responses(success_model: Type[BaseModel]) -> Dict:
    """Returns standard response models for endpoints"""
    return {
        200: {"model": success_model},
        404: {"model": HTTPError},
        400: {"model": HTTPError}
    }
