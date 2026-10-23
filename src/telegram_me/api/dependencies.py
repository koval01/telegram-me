"""FastAPI dependencies: services, feature gates and path parameters."""

from typing import Annotated, Literal

from fastapi import Depends, Path, Query, Request

from telegram_me.api.errors import FeatureDisabledError
from telegram_me.container import Container
from telegram_me.services.channels import ChannelService
from telegram_me.services.feed import FeedService
from telegram_me.validation import CHANNEL_PATTERN, MAX_POST_ID


def get_container(request: Request) -> Container:
    """The container created by the application lifespan."""
    container: Container = request.app.state.container
    return container


def get_channel_service(container: Annotated[Container, Depends(get_container)]) -> ChannelService:
    """Channel service of the running application."""
    return container.channels


def require_previews(container: Annotated[Container, Depends(get_container)]) -> None:
    """Reject the request with 503 when previews are disabled."""
    if not container.settings.enable_previews:
        raise FeatureDisabledError("previews")


def get_feed_service(container: Annotated[Container, Depends(get_container)]) -> FeedService:
    """Feed service; rejects the request with 503 when the feed is disabled."""
    if not container.settings.enable_feed:
        raise FeatureDisabledError("feed")
    return container.feed


Channels = Annotated[ChannelService, Depends(get_channel_service)]
Feed = Annotated[FeedService, Depends(get_feed_service)]
Previews = Depends(require_previews)

ChannelName = Annotated[
    str, Path(pattern=CHANNEL_PATTERN, description="Telegram channel username, e.g. `durov`")
]
PostId = Annotated[int, Path(gt=0, le=MAX_POST_ID, description="Post ID")]
Position = Annotated[int, Path(gt=0, le=MAX_POST_ID, description="Anchor post ID")]
Direction = Annotated[
    Literal["after", "before"], Path(description="Load posts newer (`after`) or older (`before`)")
]
OptionalPosition = Annotated[
    int | None,
    Query(gt=0, le=MAX_POST_ID, description="Open the page around this post ID"),
]
