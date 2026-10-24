"""HTTP endpoints."""

from typing import Any

from fastapi import APIRouter, Response

from telegram_me.api.dependencies import (
    ChannelName,
    Channels,
    Direction,
    Features,
    Feed,
    OptionalPosition,
    Position,
    PostId,
)
from telegram_me.schemas.channel import ChannelBody, More, Preview
from telegram_me.schemas.channel import Previews as PreviewsModel
from telegram_me.schemas.post import Post
from telegram_me.schemas.requests import (
    ChannelsRequest,
    FeedRequest,
    FeedResponse,
    HealthzResponse,
    HTTPError,
)
from telegram_me.services.channels import dump_json

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": HTTPError},
    404: {"model": HTTPError},
    502: {"model": HTTPError, "description": "Unexpected response from t.me"},
    503: {"model": HTTPError, "description": "t.me unavailable or feature disabled"},
}


def _json(model: Preview | PreviewsModel) -> Response:
    # Models are validated once and serialized by pydantic-core directly;
    # returning a Response skips FastAPI's second validation pass.
    return Response(dump_json(model), media_type="application/json")


def _raw_json(content: bytes) -> Response:
    return Response(content, media_type="application/json")


service = APIRouter(tags=["Service"])
channels = APIRouter(prefix="/v1", tags=["Channel"])
feed = APIRouter(prefix="/v1", tags=["Feed"])


@service.get("/healthz", summary="Health status")
async def healthz() -> HealthzResponse:
    """Liveness probe."""
    return HealthzResponse(status="ok")


@channels.get(
    "/body/{channel}",
    summary="Get basic information about the channel",
    response_model=ChannelBody,
    responses=_ERRORS,
)
async def body(
    channel: ChannelName, service: Channels, position: OptionalPosition = None
) -> Response:
    """Channel profile, its latest posts (or posts around `position`) and pagination."""
    return _raw_json(await service.body_json(channel, position))


@channels.get(
    "/more/{channel}/{direction}/{position}",
    summary="Get more posts from the channel",
    response_model=More,
    responses=_ERRORS,
)
async def more(
    channel: ChannelName, direction: Direction, position: Position, service: Channels
) -> Response:
    """Posts newer or older than `position`; use `meta.offset` to keep paginating."""
    return _raw_json(await service.more_json(channel, position, direction))


@channels.get(
    "/post/{channel}/{identifier}",
    summary="Get one post from the channel",
    response_model=Post,
    responses=_ERRORS,
)
async def post(channel: ChannelName, identifier: PostId, service: Channels) -> Response:
    """A single post."""
    return _raw_json(await service.post_json(channel, identifier))


@channels.get(
    "/preview/{channel}",
    summary="Get preview information of channel",
    response_model=Preview,
    responses=_ERRORS,
)
async def preview(channel: ChannelName, service: Channels, features: Features) -> Response:
    """Short channel summary: title, subscribers, description, avatar."""
    features.require("previews")
    return _json(await service.preview(channel))


@channels.post(
    "/previews",
    summary="Get preview data about the channel group",
    response_model=PreviewsModel,
    responses=_ERRORS,
)
async def previews(payload: ChannelsRequest, service: Channels, features: Features) -> Response:
    """Previews of up to 100 channels at once; unknown channels are omitted."""
    features.require("previews")
    return _json(await service.previews(payload.channels))


@feed.post("/feed", response_model=FeedResponse, responses=_ERRORS)
async def create_feed(item: FeedRequest, service: Feed, features: Features) -> Response:
    """Create feed for given channels and return sorted posts."""
    features.require("feed")
    return _raw_json(await service.build_json(item.channels))
