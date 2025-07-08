from __future__ import annotations
from typing import List

from pydantic import BaseModel

from app.telegram.models.post import Post
from app.telegram.models.meta import Meta


class More(BaseModel):
    """Represents a paginated batch of Telegram channel content with associated metadata.

    This model is typically used when fetching additional content from a Telegram channel
    that couldn't be loaded in the initial request, such as when scrolling through
    historical posts or loading more search results.

    Attributes:
        posts (List[Post]):
            A collection of Telegram post objects representing the additional content.
            Each Post contains the complete structure of a channel message including:
            - Content (text, media, polls, etc.)
            - Metadata (author, date, views)
            - Engagement data (reactions, forwards)

        meta (Meta):
            Pagination and context metadata about the batch of posts, which may include:
            - Total available posts count
            - Pagination tokens/cursors
            - Loading status indicators
            - Channel context information

    Example:
        ```python
        {
            "posts": [
                {...},  # Post objects
                {...}
            ],
            "meta": {
                "has_more": True,
                "next_offset": "1234567890"
            }
        }
        ```
    """

    posts: List[Post]
    meta: Meta
