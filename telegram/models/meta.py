from __future__ import annotations
from typing import List, Optional

from pydantic import BaseModel


class OffsetItem(BaseModel):
    before: Optional[int] = None
    after: Optional[int] = None


class Meta(BaseModel):
    offset: List[OffsetItem]
