"""
Common models
"""

from pydantic import BaseModel


class ParsedAndRaw(BaseModel):
    """
    Model for parsed and raw data
    """
    string: str
    html: str
