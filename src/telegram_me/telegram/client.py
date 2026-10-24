"""Access to t.me web pages.

Networking is delegated to a `Fetcher`. The production fetcher is backed by
pyreqwest, so HTTP/2, TLS, connection pooling and decompression run in Rust
(tokio + hyper + rustls) outside the GIL; Python only awaits the result and
hands the HTML to the lexbor-based parser.
"""

import json
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Literal, NamedTuple, Protocol

from pyreqwest.client import Client, ClientBuilder
from pyreqwest.exceptions import NetworkError, PyreqwestError

from telegram_me.services.upstream import FairGate
from telegram_me.telegram.errors import (
    TelegramNotFoundError,
    TelegramParseError,
    TelegramUpstreamError,
    TelegramValidationError,
)
from telegram_me.validation import MAX_POST_ID, is_valid_channel

logger = logging.getLogger(__name__)

type Method = Literal["GET", "POST"]
type Direction = Literal["before", "after"]

TELEGRAM_BASE_URL = "https://t.me"
TELEGRAM_HEADERS: dict[str, str] = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-language": "ru",
    "cache-control": "max-age=0",
    "dnt": "1",
    "priority": "u=0, i",
    "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": "Android",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Mobile Safari/537.36"
    ),
}


class FetchResult(NamedTuple):
    """Status code and decoded body of an upstream response."""

    status: int
    text: str


class Fetcher(Protocol):
    """Performs raw HTTP requests against t.me."""

    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        """Send one request; raise `TelegramUpstreamError` on transport failures."""
        ...

    async def close(self) -> None:
        """Release connections."""
        ...


class ReqwestFetcher:
    """`Fetcher` backed by a shared pyreqwest client (HTTP/2, pooled, gzip/br/zstd)."""

    def __init__(self, timeout: float, base_url: str = TELEGRAM_BASE_URL) -> None:
        self._client: Client = (
            ClientBuilder()
            .base_url(base_url)
            .default_headers(TELEGRAM_HEADERS)
            .http2(True)
            .gzip(True)
            .brotli(True)
            .zstd(True)
            .deflate(True)
            .follow_redirects(False)
            .timeout(timedelta(seconds=timeout))
            .connect_timeout(timedelta(seconds=min(timeout, 5.0)))
            .pool_idle_timeout(timedelta(seconds=90))
            .build()
        )

    async def fetch(  # noqa: D102
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        builder = self._client.request(method, path)
        if query:
            builder = builder.query(dict(query))
        if headers:
            builder = builder.headers(dict(headers))
        if method == "POST":
            # t.me rejects body-less POSTs with "411 Length Required".
            builder = builder.body_bytes(b"")
        try:
            async with builder.build_streamed() as response:
                return FetchResult(response.status, await response.text())
        except PyreqwestError as exc:
            raise TelegramUpstreamError("Failed to fetch Telegram upstream resource") from exc

    async def close(self) -> None:  # noqa: D102
        await self._client.close()


class TelegramClient:
    """Typed access to the three kinds of t.me pages the API is built on."""

    def __init__(
        self, fetcher: Fetcher, max_concurrency: int, gate: FairGate | None = None
    ) -> None:
        self._fetcher = fetcher
        # Caps simultaneous requests to t.me across the whole process, so one
        # large feed or previews call can not trigger upstream rate limiting,
        # and shares the slots fairly (see `services.upstream`).
        self.gate = gate or FairGate(max_concurrency)

    async def channel_page(self, channel: str, position: int | None = None) -> str:
        """HTML of `t.me/s/<channel>`, optionally centered around post `position`."""
        _check_channel(channel)
        if position is not None:
            _check_position(position)
        path = f"/s/{channel}/{position}" if position else f"/s/{channel}"
        return await self._get_text("GET", path, flow=channel)

    async def more_page(self, channel: str, position: int, direction: Direction) -> str:
        """HTML fragment with posts before/after `position` (infinite scroll endpoint)."""
        _check_channel(channel)
        _check_position(position)
        raw = await self._get_text(
            "POST",
            f"/s/{channel}",
            query={direction: position},
            headers={"x-requested-with": "XMLHttpRequest"},
            flow=channel,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TelegramParseError("Failed to parse Telegram more response") from exc
        if isinstance(payload, dict):
            return str(payload.get("html", ""))
        return str(payload)

    async def preview_page(self, channel: str) -> str:
        """HTML of the `t.me/<channel>` landing page."""
        _check_channel(channel)
        return await self._get_text("GET", f"/{channel}", flow=channel)

    async def close(self) -> None:
        """Close the underlying fetcher."""
        await self._fetcher.close()

    async def _get_text(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
        flow: str,
    ) -> str:
        async with self.gate.slot(flow.lower()):
            try:
                result = await self._fetcher.fetch(method, path, query=query, headers=headers)
            except TelegramUpstreamError as exc:
                if not isinstance(exc.__cause__, NetworkError):
                    raise
                # A pooled HTTP/2 connection may have been closed by t.me
                # (GOAWAY); one immediate retry on a fresh connection is safe
                # because every request we send is read-only.
                logger.debug("Retrying %s %s after network error: %s", method, path, exc)
                result = await self._fetcher.fetch(method, path, query=query, headers=headers)

        if result.status == 404:
            raise TelegramNotFoundError("Requested Telegram resource was not found")
        if result.status >= 400:
            raise TelegramUpstreamError(f"Telegram upstream returned status {result.status}")
        return result.text


def _check_channel(channel: str) -> None:
    if not is_valid_channel(channel):
        raise TelegramValidationError("Invalid Telegram username format")


def _check_position(position: int) -> None:
    if not 0 < position <= MAX_POST_ID:
        raise TelegramValidationError("Invalid post position")
