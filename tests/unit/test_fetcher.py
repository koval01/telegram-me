"""Both fetchers against a real local HTTP server (no internet required)."""

import asyncio
import gzip
import json
import re
import socketserver
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, override

import pytest
from pydantic import ValidationError

from telegram_me.container import build_container
from telegram_me.telegram.client import (
    BrowserFetcher,
    Fetcher,
    ReqwestFetcher,
    create_fetcher,
)
from telegram_me.telegram.errors import TelegramNetworkError, TelegramUpstreamError
from tests.conftest import make_settings

type FetcherFactory = Callable[..., Fetcher]

FETCHERS: dict[str, FetcherFactory] = {"reqwest": ReqwestFetcher, "browser": BrowserFetcher}

# Released by the server fixture on teardown, so `/slow` handlers end.
_release = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    def _reply(self) -> None:
        if self.path.startswith("/slow"):
            _release.wait(10)
            return
        length = int(self.headers.get("content-length") or 0)
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "headers": [[name.lower(), value] for name, value in self.headers.items()],
                "content_length": self.headers.get("content-length"),
                "body": self.rfile.read(length).decode(),
            }
        ).encode()
        status = 200
        if self.path.startswith("/missing"):
            status = 404
        elif self.path.startswith("/forbidden"):
            status = 403
        body = gzip.compress(payload)
        self.send_response(status)
        if self.path.startswith("/cookie"):
            # As t.me does on every page, minus `secure` (this is plain HTTP).
            self.send_header("set-cookie", "stel_ssid=session; path=/; samesite=None; HttpOnly")
        self.send_header("content-type", "application/json")
        self.send_header("content-encoding", "gzip")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    protocol_version = "HTTP/1.0"  # one request per connection: nothing left open

    do_GET = _reply  # noqa: N815
    do_POST = _reply  # noqa: N815

    @override
    def log_message(self, *_args: object) -> None:
        return


class _Server(ThreadingHTTPServer):
    @override
    def server_bind(self) -> None:
        # HTTPServer.server_bind() resolves the host name (getfqdn), which can
        # take tens of seconds on some machines; it is not needed here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    _release.clear()
    server = _Server(("127.0.0.1", 0), _Handler)
    server.daemon_threads = False
    server.block_on_close = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    _release.set()
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(params=sorted(FETCHERS))
def make_fetcher(request: pytest.FixtureRequest) -> FetcherFactory:
    factory: FetcherFactory = FETCHERS[request.param]
    return factory


async def _echo(fetcher: Fetcher, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        result = await fetcher.fetch(*args, **kwargs)
    finally:
        await fetcher.close()
    assert result.status == 200
    echo: dict[str, Any] = json.loads(result.text)
    return echo


def _headers(echo: dict[str, Any]) -> dict[str, str]:
    return dict(echo["headers"])


async def test_get_decodes_gzip_and_sends_profile_headers(
    server_url: str, make_fetcher: FetcherFactory
) -> None:
    echo = await _echo(
        make_fetcher(5, base_url=server_url), "GET", "/s/durov", query={"before": 10}
    )
    headers = _headers(echo)
    assert echo["path"] == "/s/durov?before=10"
    assert "Chrome/156." in headers["user-agent"]
    assert "Mobile" in headers["user-agent"]
    assert 'v="156"' in headers["sec-ch-ua"]
    assert headers["sec-ch-ua-mobile"] == "?1"
    assert headers["sec-ch-ua-platform"] == '"Android"'
    assert headers["accept-language"] == "ru"
    assert headers["sec-fetch-mode"] == "navigate"
    assert "x-requested-with" not in headers


async def test_post_sends_empty_body_with_length(
    server_url: str, make_fetcher: FetcherFactory
) -> None:
    echo = await _echo(
        make_fetcher(5, base_url=server_url),
        "POST",
        "/s/durov",
        query={"after": 5},
        headers={"x-requested-with": "XMLHttpRequest"},
    )
    assert echo["method"] == "POST"
    assert echo["path"] == "/s/durov?after=5"
    assert _headers(echo)["x-requested-with"] == "XMLHttpRequest"
    # pyreqwest sends no length over HTTP/1 (HTTP/2 adds it): what t.me needs.
    assert int(echo["content_length"] or 0) == 0
    assert echo["body"] == ""


async def test_error_status_is_returned_not_raised(
    server_url: str, make_fetcher: FetcherFactory
) -> None:
    fetcher = make_fetcher(5, base_url=server_url)
    try:
        result = await fetcher.fetch("GET", "/missing")
    finally:
        await fetcher.close()
    assert result.status == 404


async def test_connection_failure_is_network_error(make_fetcher: FetcherFactory) -> None:
    fetcher = make_fetcher(2, base_url="http://127.0.0.1:9")
    try:
        with pytest.raises(TelegramNetworkError):
            await fetcher.fetch("GET", "/s/durov")
    finally:
        await fetcher.close()


async def test_timeout_is_not_retried_as_network_error(
    server_url: str, make_fetcher: FetcherFactory
) -> None:
    fetcher = make_fetcher(0.3, base_url=server_url)
    try:
        with pytest.raises(TelegramUpstreamError) as caught:
            await fetcher.fetch("GET", "/slow")
    finally:
        await fetcher.close()
    assert not isinstance(caught.value, TelegramNetworkError)


async def test_browser_headers_follow_the_profile_order(server_url: str) -> None:
    fetcher = BrowserFetcher(5, base_url=server_url)
    expected = [name for name, _ in fetcher._client.headers]
    echo = await _echo(fetcher, "GET", "/s/durov")
    sent = [name for name, _ in echo["headers"] if name not in {"host", "connection"}]
    assert sent == expected
    headers = _headers(echo)
    version = re.search(r"Chrome/(\d+)\.", headers["user-agent"])
    assert version is not None
    assert f'"Chromium";v="{version[1]}"' in headers["sec-ch-ua"]


async def test_browser_xhr_looks_like_the_page_script(server_url: str) -> None:
    echo = await _echo(
        BrowserFetcher(5, base_url=server_url),
        "POST",
        "/s/durov",
        query={"before": 10},
        headers={"x-requested-with": "XMLHttpRequest"},
    )
    headers = _headers(echo)
    assert headers["content-length"] == "0"
    assert headers["sec-fetch-site"] == "same-origin"
    assert headers["sec-fetch-mode"] == "cors"
    assert headers["sec-fetch-dest"] == "empty"
    assert headers["origin"] == server_url
    assert headers["referer"] == f"{server_url}/s/durov"
    assert headers["accept"].startswith("application/json")
    assert "sec-fetch-user" not in headers
    assert "upgrade-insecure-requests" not in headers


async def test_browser_keeps_cookies_and_renews_the_session(server_url: str) -> None:
    fetcher = BrowserFetcher(5, base_url=server_url)
    try:
        await fetcher.fetch("GET", "/cookie")
        page = json.loads((await fetcher.fetch("GET", "/s/durov")).text)
        assert _headers(page)["cookie"] == "stel_ssid=session"
        more = await fetcher.fetch(
            "POST", "/s/durov", query={"before": 9}, headers={"x-requested-with": "XMLHttpRequest"}
        )
        assert _headers(json.loads(more.text))["cookie"] == "stel_ssid=session"
        assert fetcher._client.session == 1
        # A 4xx other than 404 ends the identity: no cookie on the next request.
        assert (await fetcher.fetch("GET", "/forbidden")).status == 403
        page = json.loads((await fetcher.fetch("GET", "/s/durov")).text)
        assert "cookie" not in _headers(page)
        assert fetcher._client.session == 2
    finally:
        await fetcher.close()


async def test_session_lifetime_reaches_the_browser_fetcher(server_url: str) -> None:
    settings = make_settings(telegram_base_url=server_url, telegram_session_lifetime=0.05)
    fetcher = create_fetcher(settings)
    try:
        await fetcher.fetch("GET", "/cookie")
        await asyncio.sleep(0.1)  # longer than 1.25 x the lifetime
        page = json.loads((await fetcher.fetch("GET", "/s/durov")).text)
        assert "cookie" not in _headers(page)
    finally:
        await fetcher.close()


def test_session_lifetime_is_not_negative() -> None:
    assert make_settings(telegram_session_lifetime=0).telegram_session_lifetime == 0
    with pytest.raises(ValidationError, match="telegram_session_lifetime"):
        make_settings(telegram_session_lifetime=-1)


async def test_closed_browser_fetcher_raises_upstream_error() -> None:
    fetcher = BrowserFetcher(1)
    await fetcher.close()
    with pytest.raises(TelegramUpstreamError):
        await fetcher.fetch("GET", "/s/durov")


@pytest.mark.parametrize(
    ("kind", "expected"), [("reqwest", ReqwestFetcher), ("browser", BrowserFetcher)]
)
async def test_setting_selects_the_fetcher(kind: str, expected: type) -> None:
    settings = make_settings(telegram_client=kind, telegram_browser_platform="windows")
    fetcher = create_fetcher(settings)
    try:
        assert isinstance(fetcher, expected)
    finally:
        await fetcher.close()


def test_unknown_profiles_are_rejected_at_startup() -> None:
    for profile in ("chrome_999", "firefox_151"):
        with pytest.raises(ValidationError, match="newest Chrome is chrome_"):
            make_settings(telegram_browser_profile=profile)


@pytest.mark.parametrize("kind", sorted(FETCHERS))
async def test_container_fetches_from_telegram_base_url(server_url: str, kind: str) -> None:
    settings = make_settings(telegram_base_url=server_url, telegram_client=kind)
    async with build_container(settings) as container:
        page = await container.client.preview_page("durov")
    assert json.loads(page)["path"] == "/durov"


@pytest.mark.parametrize("url", ["t.me", "https://t.me/s", "ftp://t.me", "https://t.me?x=1"])
def test_telegram_base_url_is_an_origin(url: str) -> None:
    with pytest.raises(ValidationError, match="telegram_base_url"):
        make_settings(telegram_base_url=url)
