"""Access to t.me web pages.

Networking is delegated to a `Fetcher`; `TELEGRAM_CLIENT` picks one:

- `ReqwestFetcher` (pyreqwest: tokio, hyper, rustls) sends the headers of a
  browser profile, but its TLS and HTTP/2 fingerprints are those of rustls and
  hyper, not of the browser the headers claim;
- `BrowserFetcher` (native, wreq on BoringSSL) takes the TLS handshake, the
  HTTP/2 settings and the headers from one profile, so they agree.

Both run the request in Rust outside the GIL; Python only awaits the result and
hands the HTML to the native parser.
"""

import json
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Literal, NamedTuple, Protocol

from pyreqwest.client import Client, ClientBuilder
from pyreqwest.exceptions import NetworkError, PyreqwestError
from telegram_me_native import (
    BrowserClient,
    FetchError,
    FetchNetworkError,
    browser_headers,
)

from telegram_me.services.upstream import FairGate
from telegram_me.settings import Settings
from telegram_me.telegram.errors import (
    TelegramNetworkError,
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
TELEGRAM_ACCEPT_LANGUAGE = "ru"
DEFAULT_BROWSER_PROFILE = "chrome_149"
DEFAULT_BROWSER_PLATFORM = "android"


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
    """`Fetcher` backed by a shared pyreqwest client (HTTP/2, pooled, gzip/br/zstd).

    It sends the navigation headers of a browser profile; its TLS and HTTP/2
    fingerprints stay those of rustls and hyper (see `BrowserFetcher`).
    """

    def __init__(
        self,
        timeout: float,
        base_url: str = TELEGRAM_BASE_URL,
        *,
        profile: str = DEFAULT_BROWSER_PROFILE,
        platform: str = DEFAULT_BROWSER_PLATFORM,
    ) -> None:
        headers = dict(browser_headers(profile, platform, TELEGRAM_ACCEPT_LANGUAGE))
        self._client: Client = (
            ClientBuilder()
            .base_url(base_url)
            .default_headers(headers)
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
        except NetworkError as exc:
            raise TelegramNetworkError("Connection to Telegram upstream failed") from exc
        except PyreqwestError as exc:
            raise TelegramUpstreamError("Failed to fetch Telegram upstream resource") from exc

    async def close(self) -> None:  # noqa: D102
        await self._client.close()


class BrowserFetcher:
    """`Fetcher` that looks like one browser on the wire (native, wreq).

    The TLS handshake (BoringSSL), HTTP/2 settings and headers all come from one
    profile; connections are pooled HTTP/2, bodies gzip/br/zstd. A POST with
    `x-requested-with` goes out as the channel page's XHR, the way a browser
    sends the "load more" call.
    """

    def __init__(
        self,
        timeout: float,
        base_url: str = TELEGRAM_BASE_URL,
        *,
        profile: str = DEFAULT_BROWSER_PROFILE,
        platform: str = DEFAULT_BROWSER_PLATFORM,
    ) -> None:
        self._client = BrowserClient(
            base_url=base_url,
            profile=profile,
            platform=platform,
            accept_language=TELEGRAM_ACCEPT_LANGUAGE,
            timeout=timeout,
        )

    async def fetch(  # noqa: D102
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        try:
            status, text = await self._client.fetch(
                method,
                path,
                list(query.items()) if query else [],
                list(headers.items()) if headers else [],
            )
        except FetchNetworkError as exc:
            raise TelegramNetworkError("Connection to Telegram upstream failed") from exc
        except FetchError as exc:
            raise TelegramUpstreamError("Failed to fetch Telegram upstream resource") from exc
        return FetchResult(status, text)

    async def close(self) -> None:  # noqa: D102
        self._client.close()


def create_fetcher(settings: Settings) -> Fetcher:
    """The fetcher `TELEGRAM_CLIENT` selects."""
    fetcher = BrowserFetcher if settings.telegram_client == "browser" else ReqwestFetcher
    return fetcher(
        settings.telegram_timeout,
        settings.telegram_base_url,
        profile=settings.telegram_browser_profile,
        platform=settings.telegram_browser_platform,
    )


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
            except TelegramNetworkError as exc:
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
