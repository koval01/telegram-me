from typing import List
from fastapi import APIRouter
from pydantic import BaseModel, field_validator
import re

from app.utils.feed import PostDataPreparer

router = APIRouter(tags=["Feed"])

class FeedRequest(BaseModel):
    channels: List[str]

    @field_validator("channels")
    def validate_channels(cls, v: List[str]) -> List[str]:
        if not (1 <= len(v) <= 100):
            raise ValueError("The number of channels must be between 1 and 100")

        cleaned = []
        for channel in v:
            channel = channel.strip()
            if not channel:
                raise ValueError("Channel name cannot be empty")
            if len(channel) < 3 or len(channel) > 32:
                raise ValueError(f"Channel '{channel}' must be between 4 and 32 characters")
            if channel[0] == "_":
                raise ValueError(f"Channel '{channel}' cannot start with an underscore")
            if not re.match(r'^[A-Za-z0-9_]+$', channel):
                raise ValueError(f"Channel '{channel}' contains invalid characters")
            cleaned.append(channel)
        return cleaned

@router.post("/feed")
async def create_feed(item: FeedRequest) -> dict:
    preparer = PostDataPreparer()
    all_posts = await preparer.prepare_multiple_channels(item.channels)
    all_posts.sort(key=lambda post: post.get("_score", 0), reverse=True)
    return {"result": all_posts}
