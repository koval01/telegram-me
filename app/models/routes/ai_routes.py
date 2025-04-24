from pydantic import field_validator
from typing import Literal

from app.models.routes.base import BaseRequestWithId


class GenerateRequest(BaseRequestWithId):
    lang: Literal["en", "de", "ru", "uk"]
