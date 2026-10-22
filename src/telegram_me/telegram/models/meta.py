from __future__ import annotations
from typing import Optional

from pydantic import BaseModel


class OffsetItem(BaseModel):
    """Represents pagination cursors for navigating through content.

    These offsets are typically used in API responses to enable cursor-based pagination,
    allowing efficient navigation through large datasets without traditional page numbers.

    Attributes:
        before (Optional[int]): A cursor pointing to the previous item in the sequence.
            When present, indicates there is content before this position.
            Use this to fetch earlier items in paginated results.
        after (Optional[int]): A cursor pointing to the next item in the sequence.
            When present, indicates there is more content after this position.
            Use this to fetch subsequent items in paginated results.
    """
    before: Optional[int] = None
    after: Optional[int] = None


class Meta(BaseModel):
    """Contains pagination metadata for API responses.

    This model wraps pagination information that helps clients navigate through
    paginated content efficiently. The offset-based approach is particularly
    useful for real-time data that may change between requests.

    Attributes:
        offset (OffsetItem): Container for pagination cursors, providing
            navigation points to adjacent content segments. Contains both
            'before' and 'after' cursors when applicable.
    """
    offset: OffsetItem
