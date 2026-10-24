"""Per-request GraphQL context: services plus a memo of their results."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Depends
from strawberry.fastapi import BaseContext

from telegram_me.api.dependencies import get_container
from telegram_me.container import Container
from telegram_me.services.channels import decode
from telegram_me.telegram.parsing.pages import JSONDict

if TYPE_CHECKING:
    from telegram_me.settings import Settings


class RequestLoader:
    """Service calls memoized for one HTTP request.

    Equal calls, from aliased fields, repeated fragments or the operations of
    a batch, share one result, so each page is fetched (or read from the
    cache) at most once per request; channel names compare case-insensitively.
    Across requests, and with the REST endpoints, the services coalesce.
    Results are the JSON-compatible data the REST endpoints serialize; callers
    must treat them as read-only.
    """

    def __init__(self, container: Container) -> None:
        self._channels = container.channels
        self._feed = container.feed
        self._calls: dict[tuple[object, ...], asyncio.Task[Any]] = {}

    async def _once[T](
        self, key: tuple[object, ...], factory: Callable[[], Coroutine[Any, Any, T]]
    ) -> T:
        task = self._calls.get(key)
        if task is None:
            task = self._calls[key] = asyncio.ensure_future(factory())
            task.add_done_callback(_retrieve_exception)
        # A cancelled caller must not cancel the result shared with other fields.
        result: T = await asyncio.shield(task)
        return result

    async def body(self, channel: str, position: int | None) -> JSONDict:
        """Channel page, as returned by `GET /v1/body/{channel}`."""
        return await self._once(
            ("body", channel.lower(), position or 0), lambda: self._body(channel, position)
        )

    async def _body(self, channel: str, position: int | None) -> JSONDict:
        data: JSONDict = decode(await self._channels.body_json(channel, position))
        return data

    async def more(
        self, channel: str, position: int, direction: Literal["after", "before"]
    ) -> JSONDict:
        """Posts around `position`, as returned by `GET /v1/more/...`."""
        return await self._once(
            ("more", channel.lower(), position, direction),
            lambda: self._more(channel, position, direction),
        )

    async def _more(
        self, channel: str, position: int, direction: Literal["after", "before"]
    ) -> JSONDict:
        data: JSONDict = decode(await self._channels.more_json(channel, position, direction))
        return data

    async def post(self, channel: str, identifier: int) -> JSONDict:
        """One post, as returned by `GET /v1/post/{channel}/{identifier}`."""
        return await self._once(
            ("post", channel.lower(), identifier), lambda: self._post(channel, identifier)
        )

    async def _post(self, channel: str, identifier: int) -> JSONDict:
        data: JSONDict = decode(await self._channels.post_json(channel, identifier))
        return data

    async def previews(self, channels: tuple[str, ...]) -> dict[str, JSONDict]:
        """Previews keyed by username, as returned by `POST /v1/previews`."""
        return await self._once(("previews", channels), lambda: self._previews(channels))

    async def _previews(self, channels: tuple[str, ...]) -> dict[str, JSONDict]:
        previews = await self._channels.previews(list(channels))
        return {name: preview.model_dump(mode="json") for name, preview in previews.root.items()}

    async def preview(self, channel: str) -> JSONDict:
        """Channel preview, as returned by `GET /v1/preview/{channel}`."""
        return await self._once(("preview", channel.lower()), lambda: self._preview(channel))

    async def _preview(self, channel: str) -> JSONDict:
        return (await self._channels.preview(channel)).model_dump(mode="json")

    async def feed(self, channels: tuple[str, ...]) -> list[JSONDict]:
        """Ranked feed posts, as returned by `POST /v1/feed` (`result`)."""
        return await self._once(("feed", channels), lambda: self._feed.build(list(channels)))


def _retrieve_exception(task: asyncio.Task[Any]) -> None:
    # Mark the exception as retrieved even if every awaiting caller was
    # cancelled, so asyncio does not log "exception was never retrieved".
    if not task.cancelled():
        task.exception()


class GraphQLContext(BaseContext):
    """Context of one GraphQL HTTP request (shared by the operations of a batch)."""

    def __init__(self, container: Container) -> None:
        super().__init__()
        self.settings: Settings = container.settings
        self.loader = RequestLoader(container)


async def get_context(
    container: Annotated[Container, Depends(get_container)],
) -> GraphQLContext:
    """FastAPI dependency building the GraphQL context."""
    return GraphQLContext(container)
