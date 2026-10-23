"""Channel pages, posts and previews with caching and request coalescing."""

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from pydantic_core import from_json, to_json

from telegram_me.cache import Cache, CacheEntry
from telegram_me.schemas.channel import ChannelBody, More, Preview, Previews
from telegram_me.schemas.post import Post
from telegram_me.services.coalesce import SingleFlight
from telegram_me.settings import Settings
from telegram_me.telegram.client import TelegramClient
from telegram_me.telegram.errors import (
    TelegramError,
    TelegramNotFoundError,
    TelegramParseError,
)
from telegram_me.telegram.parsing.html import JSONDict
from telegram_me.telegram.parsing.pages import (
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


def _rendered(page_key: str) -> str:
    return f"json:body:{page_key}"


class ChannelService:
    """Fetches, parses, caches and validates t.me pages."""

    def __init__(self, client: TelegramClient, cache: Cache, settings: Settings) -> None:
        self._client = client
        self._cache = cache
        self._settings = settings
        self._simplified = settings.parser_mode == "simplified"
        self._pages: SingleFlight[JSONDict] = SingleFlight()
        self._previews: SingleFlight[JSONDict | None] = SingleFlight()
        self._rendered: SingleFlight[bytes] = SingleFlight()

    # Raw (parsed, unvalidated) pages -------------------------------------------------

    def page_key(self, channel: str, position: int | None = None) -> str:
        """Cache key of a parsed channel page."""
        return f"page:{self._settings.parser_mode}:{channel.lower()}:{position or 0}"

    async def cached_page(self, channel: str) -> CacheEntry | None:
        """Cached latest page of a channel, regardless of its age."""
        return await self._cache.get(self.page_key(channel))

    async def page(self, channel: str, position: int | None = None) -> JSONDict:
        """Parsed channel page, served from cache while younger than the page TTL."""
        ttl = self._settings.page_cache_ttl
        entry = await self._cache.get(self.page_key(channel, position)) if ttl else None
        if entry is not None and entry.age < ttl:
            return decode_page(entry.value)
        return await self.refresh_page(channel, position)

    async def refresh_page(self, channel: str, position: int | None = None) -> JSONDict:
        """Fetch and parse a page now (coalesced with concurrent identical fetches)."""
        key = self.page_key(channel, position)
        return await self._pages.run(key, lambda: self._load_page(key, channel, position))

    def refresh_page_in_background(self, channel: str) -> None:
        """Start refreshing a channel's latest page without waiting for it."""
        key = self.page_key(channel)
        self._pages.start(key, lambda: self._load_page(key, channel, None))

    async def _load_page(self, key: str, channel: str, position: int | None) -> JSONDict:
        html = await self._client.channel_page(channel, position)
        data = _parse(parse_channel_page, html, simplified=self._simplified)
        page_ttl = self._settings.page_cache_ttl
        # Latest pages stay long enough for the feed's stale-while-revalidate window.
        raw_ttl = page_ttl
        if position is None and self._settings.enable_feed:
            raw_ttl = max(raw_ttl, self._settings.feed_stale_ttl)
        await self._cache.set(key, encode(data), raw_ttl)
        if page_ttl:
            # Pre-render the /v1/body response: cache hits are then served as
            # bytes without decoding, validating or serializing the page again.
            try:
                rendered = dump_json(validate_with_posts(ChannelBody, data, _BODY_POSTS))
            except TelegramParseError:
                logger.warning("Channel page of %s does not match the schema", channel)
            else:
                await self._cache.set(_rendered(key), rendered, page_ttl)
        return data

    # Endpoints -----------------------------------------------------------------------

    async def body(self, channel: str, position: int | None = None) -> ChannelBody:
        """Channel profile, posts and pagination."""
        data = await self.page(channel, position)
        return validate_with_posts(ChannelBody, data, _BODY_POSTS)

    async def body_json(self, channel: str, position: int | None = None) -> bytes:
        """`body()` rendered as JSON, served from the response cache when fresh."""
        ttl = self._settings.page_cache_ttl
        if ttl:
            entry = await self._cache.get(_rendered(self.page_key(channel, position)))
            if entry is not None and entry.age < ttl:
                return entry.value
        return dump_json(await self.body(channel, position))

    async def post(self, channel: str, identifier: int) -> Post:
        """A single post."""
        data = await self.page(channel, identifier)
        for raw in data["content"]["posts"]:
            if raw.get("id") == identifier:
                try:
                    return Post.model_validate(raw)
                except ValidationError as exc:
                    msg = "Failed to parse Telegram post response"
                    raise TelegramParseError(msg) from exc
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
            return entry.value
        return await self._rendered.run(
            key, lambda: self._load_more(key, channel, position, direction)
        )

    async def _load_more(
        self, key: str, channel: str, position: int, direction: Literal["before", "after"]
    ) -> bytes:
        html = await self._client.more_page(channel, position, direction)
        data = _parse(parse_more_page, html, simplified=self._simplified)
        data = {**data, "posts": [p for p in data["posts"] if p.get("id") != position]}
        rendered = dump_json(validate_with_posts(More, data, ("posts",)))
        await self._cache.set(key, rendered, self._settings.page_cache_ttl)
        return rendered

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
        html = await self._client.preview_page(channel)
        try:
            data = parse_preview_page(html)
        except Exception as exc:
            msg = "Failed to parse Telegram preview response"
            raise TelegramParseError(msg) from exc
        if data is None:
            await self._cache.set(key, _MISSING, _MISSING_PREVIEW_TTL)
        else:
            await self._cache.set(key, encode(data), self._settings.preview_cache_ttl)
        return data

    async def previews(self, channels: list[str]) -> Previews:
        """Previews of several channels; channels that can not be resolved are omitted.

        Raises the first error only when every channel failed for a reason other
        than not existing (for example while t.me is unreachable).
        """
        unique = list(dict.fromkeys(channels))
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
