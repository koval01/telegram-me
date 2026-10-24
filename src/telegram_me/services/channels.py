"""Channel pages, posts and previews with caching and request coalescing.

Fetched pages are parsed, validated and rendered on a worker thread: the
native parser releases the GIL, and even pydantic's share must not run on the
event loop, where a burst of finished fetches would otherwise stall every
other request and push in-flight Redis commands past REDIS_TIMEOUT.

Identical concurrent requests do the work once. Each kind of result (parsed
page, rendered body, rendered post, "more" fragment, preview) has a single
flight per key in this process, and the leader of a flight takes the shared
lease (`services.lease`) so that only one process of all servers fetches a
key from t.me; the others pick its result up from the cache. Keys are
normalized first: channel names are case-insensitive, and an absent
position is the same as position 0. A cached entry close to the end of its
freshness is renewed early, in the background, by the first request that
reads it past its renewal point (see `EarlyRefresh`), so a hot key is fetched
about once per TTL and never expires under load.
"""

import asyncio
import functools
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import Executor
from typing import Any, Literal, NamedTuple

import telegram_me_native as native
from pydantic import BaseModel, ValidationError
from pydantic_core import from_json, to_json

from telegram_me.cache import Cache, CacheEntry
from telegram_me.schemas.channel import ChannelBody, More, Preview, Previews
from telegram_me.schemas.post import Post
from telegram_me.services import upstream
from telegram_me.services.coalesce import SingleFlight, flight_table
from telegram_me.services.lease import ANY_AGE, Lease, LocalLease, is_newer
from telegram_me.settings import Settings
from telegram_me.telegram.client import TelegramClient
from telegram_me.telegram.errors import (
    TelegramError,
    TelegramNotFoundError,
    TelegramParseError,
)
from telegram_me.telegram.parsing.pages import (
    JSONDict,
    parse_channel_page,
    parse_more_page,
    parse_preview_page,
)

logger = logging.getLogger(__name__)

# Negative preview results are cached briefly to absorb repeated lookups of
# channels that do not exist.
_MISSING_PREVIEW_TTL = 60
_MISSING = b"null"
_BODY_POSTS = ("content", "posts")
# Distinct keys in flight per waiting-request slot (see `ChannelService`).
_KEYS_PER_WAITER = 4
_LOCAL_LEASE = LocalLease()


def _rendered(page_key: str) -> str:
    return f"json:body:{page_key}"


class LoadedPage(NamedTuple):
    """A parsed channel page, as the page cache stores it, and when it was fetched."""

    data: JSONDict
    raw: bytes
    stored_at: float
    # The rendered `/v1/body` when it was rendered along with the page.
    rendered: bytes | None = None

    @property
    def age(self) -> float:
        """Seconds since the page was fetched."""
        return time.time() - self.stored_at


class ChannelService:
    """Fetches, parses, caches and validates t.me pages."""

    def __init__(
        self,
        client: TelegramClient,
        cache: Cache,
        settings: Settings,
        executor: Executor | None = None,
        lease: Lease | None = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self._settings = settings
        # Where pages are parsed; None is the event loop's default executor.
        self._executor = executor
        self._lease = lease or _LOCAL_LEASE
        self._simplified = settings.parser_mode == "simplified"
        self._early = native.EarlyRefresh(
            settings.early_refresh_window, int.from_bytes(os.urandom(8), "little")
        )
        # One table bounds the requests waiting for any kind of result.
        waiters = settings.max_waiting_requests
        table = flight_table(_KEYS_PER_WAITER * waiters, waiters)
        self._pages: SingleFlight[LoadedPage] = SingleFlight(table, "page|")
        self._bodies: SingleFlight[bytes] = SingleFlight(table, "body|")
        self._posts: SingleFlight[bytes] = SingleFlight(table, "post|")
        self._previews: SingleFlight[JSONDict | None] = SingleFlight(table, "preview|")
        self._mores: SingleFlight[bytes] = SingleFlight(table, "more|")

    def _shared_lease(self) -> Lease:
        # Without a page cache nothing is left for other processes to pick up.
        return self._lease if self._settings.page_cache_ttl else _LOCAL_LEASE

    # Raw (parsed, unvalidated) pages -------------------------------------------------

    def page_key(self, channel: str, position: int | None = None) -> str:
        """Cache key of a parsed channel page."""
        return f"page:{self._settings.parser_mode}:{channel.lower()}:{position or 0}"

    async def cached_page(self, channel: str) -> CacheEntry | None:
        """Cached latest page of a channel, regardless of its age."""
        return await self._cache.get(self.page_key(channel))

    async def page(self, channel: str, position: int | None = None) -> JSONDict:
        """Parsed channel page, served from cache while younger than the page TTL."""
        return (await self._loaded_page(channel, position)).data

    async def _loaded_page(self, channel: str, position: int | None) -> LoadedPage:
        ttl = self._settings.page_cache_ttl
        entry = await self._cache.get(self.page_key(channel, position)) if ttl else None
        if entry is not None and entry.age < ttl:
            self._renew_if_due(entry, channel, position)
            return LoadedPage(decode_page(entry.value), entry.value, entry.stored_at)
        return await self.refresh_page(channel, position)

    async def refresh_page(
        self, channel: str, position: int | None = None, *, fresh_for: float | None = None
    ) -> LoadedPage:
        """Fetch and parse a page now (coalesced with concurrent identical fetches).

        A copy younger than `fresh_for` seconds (default: the page TTL) that
        another process wrote meanwhile is used instead of fetching again.
        """
        key = self.page_key(channel, position)
        limit = self._settings.page_cache_ttl if fresh_for is None else fresh_for
        newer_than = time.time() - limit
        return await self._pages.run(
            key, lambda: self._load_page(key, channel, position, newer_than)
        )

    def refresh_page_in_background(self, channel: str) -> None:
        """Start refreshing a channel's latest page without waiting for it."""
        key = self.page_key(channel)
        newer_than = time.time() - self._settings.feed_fresh_ttl
        self._pages.start(
            key, lambda: self._load_page(key, channel, None, newer_than), background=True
        )

    def _renew_if_due(self, entry: CacheEntry, channel: str, position: int | None) -> None:
        """Renew a page early, in the background, once its entry is close to expiry.

        `entry` is the page or a response rendered from it (they share the
        page's store time). See `EarlyRefresh`.
        """
        key = self.page_key(channel, position)
        stored_at = entry.stored_at
        if not self._early.claim(key, entry.age, self._settings.page_cache_ttl, stored_at):
            return
        self._pages.start(
            key,
            # Any copy newer than this one means another process renewed it.
            lambda: self._load_page(key, channel, position, stored_at),
            background=True,
        )

    async def _load_page(
        self,
        key: str,
        channel: str,
        position: int | None,
        newer_than: float,
    ) -> LoadedPage:
        async def fetch() -> LoadedPage:
            html = await self._client.channel_page(channel, position)
            page_ttl = self._settings.page_cache_ttl
            data, raw, rendered = await self._offload(
                _process_page, html, channel, simplified=self._simplified
            )
            stored_at = time.time()
            # Latest pages stay long enough for the feed's stale-while-revalidate window.
            raw_ttl: float = page_ttl
            if position is None and self._settings.enable_feed:
                raw_ttl = max(raw_ttl, self._early.jitter(self._settings.feed_stale_ttl))
            await self._cache.set(key, raw, raw_ttl, stored_at=stored_at)
            if rendered is not None:
                await self._cache.set(_rendered(key), rendered, page_ttl, stored_at=stored_at)
            return LoadedPage(data, raw, stored_at, rendered)

        def adopt(entry: CacheEntry) -> LoadedPage:
            return LoadedPage(decode_page(entry.value), entry.value, entry.stored_at)

        return await self._shared_lease().run(key, fetch, adopt, newer_than)

    # Endpoints -----------------------------------------------------------------------

    async def body(self, channel: str, position: int | None = None) -> ChannelBody:
        """Channel profile, posts and pagination."""
        return ChannelBody.model_validate_json(await self.body_json(channel, position))

    async def body_json(self, channel: str, position: int | None = None) -> bytes:
        """Channel profile, posts and pagination, rendered as JSON.

        Served from the response cache while fresh; otherwise rendered once
        for all concurrent requests: along with the page fetch, or from a
        cached page in a flight of its own.
        """
        key = self.page_key(channel, position)
        rendered_key = _rendered(key)
        ttl = self._settings.page_cache_ttl
        if ttl:
            entry = await self._cache.get(rendered_key)
            if entry is not None and entry.age < ttl:
                self._renew_if_due(entry, channel, position)
                return entry.value
        page = await self._loaded_page(channel, position)
        if page.rendered is not None:
            return page.rendered
        return await self._bodies.run(rendered_key, lambda: self._render_body(rendered_key, page))

    async def _render_body(self, rendered_key: str, page: LoadedPage) -> bytes:
        ttl = self._settings.page_cache_ttl
        if ttl:
            # The process that fetched the page rendered it as well.
            entry = await self._cache.get(rendered_key)
            if entry is not None and entry.stored_at >= page.stored_at and entry.age < ttl:
                return entry.value
        rendered = await self._offload(_render_body, page.data)
        if (remaining := ttl - page.age) > 0:
            await self._cache.set(rendered_key, rendered, remaining, stored_at=page.stored_at)
        return rendered

    async def post(self, channel: str, identifier: int) -> Post:
        """A single post."""
        return Post.model_validate_json(await self.post_json(channel, identifier))

    async def post_json(self, channel: str, identifier: int) -> bytes:
        """A single post rendered as JSON; cached while its page is fresh."""
        key = f"json:post:{self._settings.parser_mode}:{channel.lower()}:{identifier}"
        ttl = self._settings.page_cache_ttl
        if ttl:
            entry = await self._cache.get(key)
            if entry is not None and entry.age < ttl:
                self._renew_if_due(entry, channel, identifier)
                return entry.value
        return await self._posts.run(key, lambda: self._render_post(key, channel, identifier))

    async def _render_post(self, key: str, channel: str, identifier: int) -> bytes:
        page = await self._loaded_page(channel, identifier)
        for raw in page.data["content"]["posts"]:
            if raw.get("id") == identifier:
                try:
                    rendered = dump_json(Post.model_validate(raw))
                except ValidationError as exc:
                    msg = "Failed to parse Telegram post response"
                    raise TelegramParseError(msg) from exc
                if (remaining := self._settings.page_cache_ttl - page.age) > 0:
                    await self._cache.set(key, rendered, remaining, stored_at=page.stored_at)
                return rendered
        msg = "Requested post was not found"
        raise TelegramNotFoundError(msg)

    async def more_json(
        self, channel: str, position: int, direction: Literal["before", "after"]
    ) -> bytes:
        """Posts before or after `position` (anchor excluded), rendered as JSON."""
        key = f"more:{self._settings.parser_mode}:{channel.lower()}:{direction}:{position}"
        ttl = self._settings.page_cache_ttl
        entry = await self._cache.get(key) if ttl else None
        if entry is not None and entry.age < ttl:
            if self._early.claim(key, entry.age, ttl, entry.stored_at):
                stored_at = entry.stored_at
                self._mores.start(
                    key,
                    lambda: self._load_more(key, channel, position, direction, stored_at),
                    background=True,
                )
            return entry.value
        newer_than = time.time() - ttl
        return await self._mores.run(
            key, lambda: self._load_more(key, channel, position, direction, newer_than)
        )

    async def _load_more(
        self,
        key: str,
        channel: str,
        position: int,
        direction: Literal["before", "after"],
        newer_than: float,
    ) -> bytes:
        async def fetch() -> bytes:
            html = await self._client.more_page(channel, position, direction)
            rendered = await self._offload(
                _process_more, html, position, simplified=self._simplified
            )
            await self._cache.set(key, rendered, self._settings.page_cache_ttl)
            return rendered

        return await self._shared_lease().run(key, fetch, lambda entry: entry.value, newer_than)

    async def preview(self, channel: str) -> Preview:
        """Channel preview from its landing page (cached for PREVIEW_CACHE_TTL)."""
        key = f"preview:{channel.lower()}"
        entry = await self._cache.get(key)
        if entry is not None:
            data = decode(entry.value)
        else:
            data = await self._previews.run(key, lambda: self._load_preview(key, channel))
        if data is None:
            msg = "Requested Telegram resource was not found"
            raise TelegramNotFoundError(msg)
        return Preview.model_validate(data)

    async def _load_preview(self, key: str, channel: str) -> JSONDict | None:
        async def fetch() -> JSONDict | None:
            html = await self._client.preview_page(channel)
            data, raw = await self._offload(_process_preview, html)
            ttl = _MISSING_PREVIEW_TTL if data is None else self._settings.preview_cache_ttl
            await self._cache.set(key, raw, self._early.jitter(ttl))
            return data

        def adopt(entry: CacheEntry) -> JSONDict | None:
            data: JSONDict | None = decode(entry.value)
            return data

        # Previews are cached regardless of CACHE_MODE: any cached one will do.
        return await self._lease.run(key, fetch, adopt, ANY_AGE)

    # Peers ---------------------------------------------------------------------------

    async def serve_peer(self, key: str, newer_than: float) -> list[str]:
        """Load `key` for another node, which asked this process as its owner.

        Joins any identical work in flight here. Returns the cache keys to
        answer with, `key` first: a page travels with its rendered body.
        Raises `TelegramError` as the load does, and `ValueError` for a key
        this process does not make (another `PARSER_MODE`, say).
        """
        entry = await self._cache.get(key)
        if entry is None or not is_newer(entry, newer_than):
            await self._load_for_peer(key, newer_than)
        return [key, _rendered(key)] if key.startswith("page:") else [key]

    async def _load_for_peer(self, key: str, newer_than: float) -> None:
        mode = self._settings.parser_mode
        match key.split(":"):
            case ["page", key_mode, channel, position] if key_mode == mode:
                at = int(position) or None
                await self._pages.run(key, lambda: self._load_page(key, channel, at, newer_than))
            case ["more", key_mode, channel, "before" | "after" as direction, position] if (
                key_mode == mode
            ):
                anchor = int(position)
                side: Literal["before", "after"] = "before" if direction == "before" else "after"
                await self._mores.run(
                    key, lambda: self._load_more(key, channel, anchor, side, newer_than)
                )
            case ["preview", channel]:
                await self._previews.run(key, lambda: self._load_preview(key, channel))
            case _:
                msg = f"not a key this process loads: {key}"
                raise ValueError(msg)

    async def _offload[**P, T](self, func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Run CPU-bound `func` on the parsing executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(func, *args, **kwargs))

    async def previews(self, channels: list[str]) -> Previews:
        """Previews of several channels; channels that can not be resolved are omitted.

        Raises the first error only when every channel failed for a reason other
        than not existing (for example while t.me is unreachable).
        """
        unique = list(dict.fromkeys(channels))
        # One flow upstream: a large batch must not crowd out other requests.
        with upstream.flow(f"previews:{id(unique)}"):
            results = await asyncio.gather(
                *(self.preview(channel) for channel in unique), return_exceptions=True
            )
        found: dict[str, Preview] = {}
        failures: list[BaseException] = []
        for channel, result in zip(unique, results, strict=True):
            if isinstance(result, Preview):
                found[channel] = result
            elif isinstance(result, TelegramNotFoundError):
                continue
            elif isinstance(result, TelegramError):
                failures.append(result)
            else:
                raise result
        if failures and len(failures) == len(unique):
            raise failures[0]
        return Previews(found)


def encode(data: Any) -> bytes:
    """Serialize JSON-compatible data to UTF-8 (Rust-backed, via pydantic-core)."""
    return to_json(data)


def dump_json(model: BaseModel) -> bytes:
    """`model.model_dump_json()` as UTF-8 bytes, without an intermediate `str`."""
    return model.__pydantic_serializer__.to_json(model)


def decode(value: bytes) -> Any:
    """Deserialize JSON produced by `encode`."""
    return from_json(value)


def decode_page(value: bytes) -> JSONDict:
    """Deserialize a cached parsed page."""
    data = from_json(value)
    if not isinstance(data, dict):
        msg = "Corrupted page in cache"
        raise TelegramParseError(msg)
    return data


def _process_page(
    html: str, channel: str, *, simplified: bool
) -> tuple[JSONDict, bytes, bytes | None]:
    """Parse a channel page into (data, its cache encoding, the rendered /v1/body).

    Runs on a worker thread. The body is pre-rendered so that cache hits are
    served as bytes without decoding, validating or serializing the page again;
    it is None when the page does not match the schema.
    """
    data = _parse(parse_channel_page, html, simplified=simplified)
    rendered = None
    try:
        rendered = _render_body(data)
    except TelegramParseError:
        logger.warning("Channel page of %s does not match the schema", channel)
    return data, encode(data), rendered


def _render_body(data: JSONDict) -> bytes:
    """The `/v1/body` response for a parsed page."""
    return dump_json(validate_with_posts(ChannelBody, data, _BODY_POSTS))


def _process_more(html: str, position: int, *, simplified: bool) -> bytes:
    """Parse a "load more" fragment into the rendered response, `position` excluded."""
    data = _parse(parse_more_page, html, simplified=simplified)
    data = {**data, "posts": [p for p in data["posts"] if p.get("id") != position]}
    return dump_json(validate_with_posts(More, data, ("posts",)))


def _process_preview(html: str) -> tuple[JSONDict | None, bytes]:
    """Parse a landing page into (data, its cache encoding); data is None for non-channels."""
    try:
        data = parse_preview_page(html)
    except Exception as exc:
        msg = "Failed to parse Telegram preview response"
        raise TelegramParseError(msg) from exc
    return data, _MISSING if data is None else encode(data)


def _parse(parser: Any, html: str, *, simplified: bool) -> JSONDict:
    try:
        data: JSONDict = parser(html, simplified=simplified)
    except TelegramError:
        raise
    except Exception as exc:
        msg = "Failed to parse Telegram channel page"
        raise TelegramParseError(msg) from exc
    return data


def validate_with_posts[M: BaseModel](
    model: type[M], data: JSONDict, posts_path: tuple[str, ...]
) -> M:
    """Validate `data`; on failure drop posts that do not validate and retry once.

    A single post with unexpected content must not make the whole page fail.
    """
    try:
        return model.model_validate(data)
    except ValidationError:
        pass

    container: JSONDict = data
    for part in posts_path[:-1]:
        container = container[part]
    valid_posts = []
    for raw in container[posts_path[-1]]:
        try:
            Post.model_validate(raw)
        except ValidationError as exc:
            logger.warning("Dropping post %s that failed validation: %s", raw.get("id"), exc)
        else:
            valid_posts.append(raw)

    patched = _replace_path(data, posts_path, valid_posts)
    try:
        return model.model_validate(patched)
    except ValidationError as exc:
        msg = "Telegram response did not match the expected schema"
        raise TelegramParseError(msg) from exc


def _replace_path(data: JSONDict, path: tuple[str, ...], value: Any) -> JSONDict:
    head, *rest = path
    if not rest:
        return {**data, head: value}
    return {**data, head: _replace_path(data[head], tuple(rest), value)}
