"""Root query and schema of the GraphQL API.

Root fields mirror the REST endpoints one to one (`body`, `more`, `post`,
`preview`, `previews`, `feed`) and resolve through the same services, so both
APIs share caching, request coalescing and validation. Root fields are
nullable: a failing field is `null` with an error while the others still
return data.
"""

from functools import partial
from typing import TYPE_CHECKING, Annotated, Any, Literal

import strawberry
from graphql import GraphQLError
from strawberry.extensions import (
    DisableIntrospection,
    MaxAliasesLimiter,
    MaxTokensLimiter,
    QueryDepthLimiter,
    SchemaExtension,
)
from strawberry.extensions.query_depth_limiter import IgnoreContext
from strawberry.schema.config import StrawberryConfig
from strawberry.types.execution import ExecutionContext

from telegram_me.api.errors import FeatureDisabledError
from telegram_me.api.graphql import types
from telegram_me.api.graphql.context import GraphQLContext
from telegram_me.api.graphql.errors import ErrorCodes, log_error
from telegram_me.api.graphql.limits import MaxComplexityLimiter
from telegram_me.settings import Settings
from telegram_me.telegram.errors import TelegramValidationError
from telegram_me.telegram.parsing.html import JSONDict
from telegram_me.validation import (
    MAX_CHANNELS_PER_REQUEST,
    MAX_POST_ID,
    validate_channel_or_raise,
)

if TYPE_CHECKING:
    from collections.abc import Callable

type Info = strawberry.Info[GraphQLContext, None]

ChannelArg = Annotated[str, strawberry.argument(description="Channel username, e.g. `durov`.")]
ChannelsArg = Annotated[
    list[str],
    strawberry.argument(description=f"1 to {MAX_CHANNELS_PER_REQUEST} channel usernames."),
]


def _channel(value: str) -> str:
    try:
        return validate_channel_or_raise(value)
    except ValueError as exc:
        raise TelegramValidationError(str(exc)) from exc


def _channels(values: list[str]) -> tuple[str, ...]:
    if not 1 <= len(values) <= MAX_CHANNELS_PER_REQUEST:
        msg = f"Provide from 1 to {MAX_CHANNELS_PER_REQUEST} channels"
        raise TelegramValidationError(msg)
    return tuple(_channel(value) for value in values)


def _post_id(value: int, name: str) -> int:
    if not 0 < value <= MAX_POST_ID:
        msg = f"'{name}' must be between 1 and {MAX_POST_ID}"
        raise TelegramValidationError(msg)
    return value


def _require(info: Info, feature: Literal["feed", "previews"]) -> None:
    settings = info.context.settings
    enabled = {"feed": settings.enable_feed, "previews": settings.enable_previews}[feature]
    if not enabled:
        raise FeatureDisabledError(feature)


async def _body(
    info: Info,
    channel: ChannelArg,
    position: Annotated[
        int | None, strawberry.argument(description="Open the page around this post ID.")
    ] = None,
) -> JSONDict:
    name = _channel(channel)
    anchor = None if position is None else _post_id(position, "position")
    return await info.context.loader.body(name, anchor)


async def _more(
    info: Info,
    channel: ChannelArg,
    direction: types.Direction,
    position: Annotated[int, strawberry.argument(description="Anchor post ID.")],
) -> JSONDict:
    name = _channel(channel)
    anchor = _post_id(position, "position")
    return await info.context.loader.more(name, anchor, direction.value)


async def _post(
    info: Info,
    channel: ChannelArg,
    id: Annotated[int, strawberry.argument(description="Post ID.")],  # noqa: A002
) -> JSONDict:
    return await info.context.loader.post(_channel(channel), _post_id(id, "id"))


async def _preview(info: Info, channel: ChannelArg) -> JSONDict:
    name = _channel(channel)
    _require(info, "previews")
    return {"username": name, **await info.context.loader.preview(name)}


async def _previews(info: Info, channels: ChannelsArg) -> list[JSONDict]:
    names = _channels(channels)
    _require(info, "previews")
    found = await info.context.loader.previews(names)
    return [{"username": name, **preview} for name, preview in found.items()]


async def _feed(info: Info, channels: ChannelsArg) -> list[JSONDict]:
    names = _channels(channels)
    _require(info, "feed")
    return await info.context.loader.feed(names)


@strawberry.type(description="Read-only access to public Telegram channels.")
class Query:
    """Root fields; each mirrors one REST endpoint."""

    body = strawberry.field(
        resolver=_body,
        graphql_type=types.ChannelBody | None,
        description="Channel profile, its latest posts (or posts around `position`) and "
        "pagination. REST: `GET /v1/body/{channel}`.",
    )
    more = strawberry.field(
        resolver=_more,
        graphql_type=types.More | None,
        description="Posts newer or older than `position` (anchor excluded); use "
        "`meta.offset` to keep paginating. REST: `GET /v1/more/{channel}/{direction}/{position}`.",
    )
    post = strawberry.field(
        resolver=_post,
        graphql_type=types.Post | None,
        description="A single post. REST: `GET /v1/post/{channel}/{identifier}`.",
    )
    preview = strawberry.field(
        resolver=_preview,
        graphql_type=types.Preview | None,
        description="Short channel summary: title, subscribers, description, avatar. "
        "REST: `GET /v1/preview/{channel}`. Requires `ENABLE_PREVIEWS`.",
    )
    previews = strawberry.field(
        resolver=_previews,
        graphql_type=list[types.Preview] | None,
        description="Previews of several channels, fetched in parallel; unknown channels are "
        "omitted. REST: `POST /v1/previews`. Requires `ENABLE_PREVIEWS`.",
    )
    feed = strawberry.field(
        resolver=_feed,
        graphql_type=list[types.FeedPost] | None,
        description="Posts of several channels in ranked feed order. "
        "REST: `POST /v1/feed`. Requires `ENABLE_FEED`.",
    )


def _read_key(source: Any, name: str) -> object:
    """Default resolver: fields of the dict-backed types are read by key."""
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name)


def _is_introspection(context: IgnoreContext) -> bool:
    return context.field_name.startswith("__")


class Schema(strawberry.Schema):
    """Schema that logs resolver errors like the REST error handlers."""

    def process_errors(  # noqa: D102
        self, errors: list[GraphQLError], execution_context: ExecutionContext | None = None
    ) -> None:
        for error in errors:
            log_error(error)


def build_schema(settings: Settings) -> Schema:
    """The GraphQL schema with the limits configured in `settings`."""
    # Factories: every operation gets its own extension instances.
    extensions: list[Callable[[], SchemaExtension]] = [
        partial(MaxTokensLimiter, settings.graphql_max_tokens),
        partial(QueryDepthLimiter, settings.graphql_max_depth, should_ignore=_is_introspection),
        partial(MaxAliasesLimiter, settings.graphql_max_aliases),
        partial(MaxComplexityLimiter, settings.graphql_max_complexity),
        ErrorCodes,
    ]
    if settings.disable_docs:
        extensions.append(DisableIntrospection)
    return Schema(
        query=Query,
        extensions=extensions,
        config=StrawberryConfig(
            auto_camel_case=False,
            default_resolver=_read_key,
            scalar_map=types.SCALARS,
            batching_config={"max_operations": settings.graphql_max_batch_size},
        ),
    )
