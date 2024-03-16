"""
Main models
"""

from pydantic import BaseModel


class HTTPError(BaseModel):
    """
    Represents an HTTP error response.

    Attributes:
        detail (str): A human-readable description of the error.
    """
    detail: str
