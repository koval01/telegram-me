from fastapi import APIRouter

from app.models.error import HTTPError
from app.models.routes.feed import FeedRequest, FeedResponse
from app.utils.features import is_feed_enabled, require_feature_enabled
from app.utils.feed import PostDataPreparer

router = APIRouter(tags=["Feed"])

@router.post(
    "/feed",
    response_model=FeedResponse,
    responses={503: {"model": HTTPError}},
)
async def create_feed(item: FeedRequest) -> FeedResponse:
    """Create feed for given channels and return sorted posts."""
    require_feature_enabled(is_feed_enabled(), "feed")
    preparer = PostDataPreparer()
    all_posts = await preparer.prepare_multiple_channels(item.channels)
    all_posts.sort(key=lambda p: p.get("_score", 0), reverse=True)

    seen = set()
    unique_posts = []

    for post in all_posts:
        key = (post["channel"]["username"], post["id"])
        if key not in seen:
            seen.add(key)
            unique_posts.append(post)

    return FeedResponse(result=unique_posts)

