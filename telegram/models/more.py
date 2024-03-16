from __future__ import annotations
from typing import List

from pydantic import BaseModel

from telegram.models.post import Post
from telegram.models.meta import Meta


class More(BaseModel):
    posts: List[Post]
    meta: Meta
