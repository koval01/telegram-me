from __future__ import annotations
from typing import Optional

from pydantic import BaseModel


class OffsetItem(BaseModel):
    """
    Represents an offset item.

    Attributes:
        before (Optional[int]): The offset before.
        after (Optional[int]): The offset after.
    """

    before: Optional[int] = None
    after: Optional[int] = None


class Meta(BaseModel):
    """
    Represents metadata associated with content.

    Attributes:
        offset (OffsetItem): Dict of offset items.
    """

    offset: OffsetItem
