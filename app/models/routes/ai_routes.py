from typing import Literal

from app.models.routes.base import BaseRequestWithId


class GenerateRequest(BaseRequestWithId):
    """Validator for LLM generation handler"""
    lang: Literal["en", "de", "ru", "uk"]
