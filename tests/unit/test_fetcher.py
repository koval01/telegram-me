"""ReqwestFetcher against a real local HTTP server (no internet required)."""

import gzip
import json
import socketserver
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from telegram_me.container import build_container
from telegram_me.telegram.client import ReqwestFetcher
from telegram_me.telegram.errors import TelegramUpstreamError
from tests.conftest import make_settings


class _Handler(BaseHTTPRequestHandler):
    def _reply(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "user_agent": self.headers.get("user-agent"),
                "ajax": self.headers.get("x-requested-with"),
                "content_length": length,
                "body": self.rfile.read(length).decode(),
            }
        ).encode()
        status = 404 if self.path.startswith("/missing") else 200
        body = gzip.compress(payload)
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-encoding", "gzip")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    protocol_version = "HTTP/1.0"  # one request per connection: nothing left open

    do_GET = _reply  # noqa: N815
    do_POST = _reply  # noqa: N815

    def log_message(self, *_args: object) -> None:
        return


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind() resolves the host name (getfqdn), which can
        # take tens of seconds on some machines; it is not needed here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    server = _Server(("127.0.0.1", 0), _Handler)
    server.daemon_threads = False
    server.block_on_close = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


async def test_get_decodes_gzip_and_sends_browser_headers(server_url: str) -> None:
    fetcher = ReqwestFetcher(timeout=5, base_url=server_url)
    try:
        result = await fetcher.fetch("GET", "/s/durov", query={"before": 10})
    finally:
        await fetcher.close()
    assert result.status == 200
    echo = json.loads(result.text)
    assert echo["path"] == "/s/durov?before=10"
    assert "Chrome" in echo["user_agent"]
    assert echo["ajax"] is None


async def test_post_sends_empty_body_with_length(server_url: str) -> None:
    fetcher = ReqwestFetcher(timeout=5, base_url=server_url)
    try:
        result = await fetcher.fetch(
            "POST", "/s/durov", headers={"x-requested-with": "XMLHttpRequest"}
        )
    finally:
        await fetcher.close()
    echo = json.loads(result.text)
    assert echo["method"] == "POST"
    assert echo["ajax"] == "XMLHttpRequest"
    assert echo["content_length"] == 0


async def test_error_status_is_returned_not_raised(server_url: str) -> None:
    fetcher = ReqwestFetcher(timeout=5, base_url=server_url)
    try:
        result = await fetcher.fetch("GET", "/missing")
    finally:
        await fetcher.close()
    assert result.status == 404


async def test_connection_failure_is_upstream_error() -> None:
    fetcher = ReqwestFetcher(timeout=2, base_url="http://127.0.0.1:9")
    try:
        with pytest.raises(TelegramUpstreamError):
            await fetcher.fetch("GET", "/s/durov")
    finally:
        await fetcher.close()


async def test_container_fetches_from_telegram_base_url(server_url: str) -> None:
    async with build_container(make_settings(telegram_base_url=server_url)) as container:
        page = await container.client.preview_page("durov")
    assert json.loads(page)["path"] == "/durov"


@pytest.mark.parametrize("url", ["t.me", "https://t.me/s", "ftp://t.me", "https://t.me?x=1"])
def test_telegram_base_url_is_an_origin(url: str) -> None:
    with pytest.raises(ValidationError, match="telegram_base_url"):
        make_settings(telegram_base_url=url)
