"""Property-based tests (hypothesis): invariants over generated input.

- Whatever t.me answers, a request ends in a response the API documents
  (200, 404 or 502), never in an unhandled error (500).
- The page wrappers fail only with `TelegramError`s on broken pages.
- The protection middleware answers any `X-PoW` header, path and method with
  a well-formed response, accepts a valid proof exactly once, and counts the
  rate limit exactly.
- The feed holds each post once, only from the requested channels, whatever
  their order.

Examples are derived from a fixed seed (reproducible in CI); the `nightly`
profile (`HYPOTHESIS_PROFILE=nightly`) searches longer with random seeds.
"""

import asyncio
import contextlib
import hashlib
import itertools
import json
import os
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from starlette.types import Message, Receive, Scope, Send
from telegram_me_native import solve_pow

from telegram_me.api.protection import _POW_REJECTIONS, Protection, ProtectionMiddleware
from telegram_me.app import create_app
from telegram_me.services.channels import _process_more, _process_page, _process_preview
from telegram_me.telegram.client import FetchResult, Method
from telegram_me.telegram.errors import TelegramError
from tests.conftest import MANIFEST, _body, make_settings

settings.register_profile(
    "default", max_examples=60, derandomize=True, deadline=None, print_blob=True
)
settings.register_profile("nightly", max_examples=2000, deadline=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


def _html(item: Mapping[str, Any]) -> str:
    text = _body(item["file"])
    # "Load more" answers are JSON strings around the HTML.
    return str(json.loads(text)) if item["file"].startswith("more_") else text


PAGES = [_html(item) for item in MANIFEST["responses"] if item["status"] == 200]
SNIPPETS = [
    '<div class="tgme_widget_message" data-post="x/18446744073709551616">',
    '<div class="tgme_widget_message" data-post="">',
    '<div class="tgme_widget_message_text js-message_text">',
    '<time datetime="9999-99-99T99:99:99+99:99">',
    '<span class="tgme_widget_message_views">1.2.3K</span>',
    '<span class="counter_value">-1</span>',
    '<a class="tme_messages_more" data-before="x" href="?before=-1">',
    '<i class="emoji" style="background-image:url(\'',
    "</div></div></div>",
    "<b><i></b>",
    "&#xD800;&#0;&bogus;",
    "\u200d\ufeff\U0001f9ea",
]


@st.composite
def broken_pages(draw: st.DrawFn) -> str:
    """A recorded t.me page with parts deleted, repeated, cut off or spliced in."""
    html = draw(st.sampled_from(PAGES))
    for _ in range(draw(st.integers(0, 4))):
        start = draw(st.integers(0, len(html)))
        end = draw(st.integers(start, min(len(html), start + 4000)))
        operation = draw(st.sampled_from(["delete", "repeat", "cut", "splice"]))
        if operation == "delete":
            html = html[:start] + html[end:]
        elif operation == "repeat":
            html = html[:end] + html[start:end] + html[end:]
        elif operation == "cut":
            html = html[:start]
        else:
            html = html[:start] + draw(st.sampled_from(SNIPPETS)) + html[start:]
    return html


@given(html=broken_pages(), simplified=st.booleans())
def test_page_wrappers_fail_only_with_telegram_errors(html: str, simplified: bool) -> None:
    for process in (
        lambda: _process_page(html, "durov", simplified=simplified),
        lambda: _process_more(html, 531, simplified=simplified),
        lambda: _process_preview(html),
    ):
        with contextlib.suppress(TelegramError):
            process()


class BrokenPageFetcher:
    """Answers every t.me request with the page the current example set."""

    def __init__(self) -> None:
        self.page = ""

    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        page = json.dumps(self.page) if method == "POST" else self.page
        return FetchResult(200, page)

    async def close(self) -> None:
        return None


@pytest.fixture(scope="module")
def broken_upstream() -> Iterator[tuple[TestClient, BrokenPageFetcher]]:
    fetcher = BrokenPageFetcher()
    app = create_app(make_settings(cache_mode="off"), fetcher=fetcher)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, fetcher


_channels = itertools.count()
QUERY = '{ body(channel: "%s") { meta { offset { before } } } }'


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
@given(html=broken_pages())
def test_any_upstream_page_gives_a_documented_response(
    broken_upstream: tuple[TestClient, BrokenPageFetcher], html: str
) -> None:
    client, fetcher = broken_upstream
    fetcher.page = html
    # A channel nobody asked for yet: nothing comes from a cache.
    channel = f"durov_p{next(_channels)}"
    responses = [
        client.get(f"/v1/body/{channel}"),
        client.get(f"/v1/post/{channel}/531"),
        client.get(f"/v1/more/{channel}/before/531"),
        client.get(f"/v1/preview/{channel}"),
        client.post("/v1/feed", json={"channels": [channel]}),
        client.post(
            "/graphql",
            json={
                "query": f'{{ body(channel: "{channel}") {{ meta {{ offset {{ before }} }} }} }}'
            },
        ),
    ]
    for response in responses:
        assert response.status_code in {200, 404, 502}, (response.url, response.text[:300])
        assert isinstance(response.json(), dict)


async def _call(app: Any, scope: Scope) -> tuple[int, dict[str, str], bytes]:
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], headers, body


async def _ok(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def _guard(**overrides: Any) -> ProtectionMiddleware:
    options = {
        "enable_pow": True,
        "pow_secret": "property-tests-only-secret-0123456789",
        "pow_difficulty": 8,
        "enable_rate_limit": True,
        "rate_limit_rpm": 100_000,
    }
    return ProtectionMiddleware(_ok, Protection.from_settings(make_settings(**options | overrides)))


def _scope(
    method: str, path: str, query: bytes, headers: list[tuple[bytes, bytes]], ip: str
) -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": headers,
        "client": (ip, 1234),
    }


_path_chars = st.characters(min_codepoint=0x21, max_codepoint=0x7E, exclude_characters="?#")
paths = st.text(_path_chars, max_size=40).map(lambda tail: f"/v1/{tail}")
# Request targets are ASCII (RFC 9112); clients percent-encode the rest.
queries = st.text(_path_chars, max_size=40).map(str.encode)
# Raw header bytes: anything, or something shaped like `<challenge>:<nonce>`.
pow_headers = st.one_of(
    st.none(),
    st.binary(max_size=120),
    st.builds(lambda a, b: f"{a}:{b}".encode(), st.text(max_size=60), st.text(max_size=20)),
)
methods = st.sampled_from(["GET", "POST", "PUT", "DELETE", "HEAD"])


@given(method=methods, path=paths, query=queries, header=pow_headers)
def test_any_proof_of_work_header_gets_a_well_formed_answer(
    method: str, path: str, query: bytes, header: bytes | None
) -> None:
    headers = [] if header is None else [(b"x-pow", header)]
    status, response_headers, body = asyncio.run(
        _call(_guard(), _scope(method, path, query, headers, "203.0.113.7"))
    )
    assert int(response_headers.get("x-ratelimit-remaining", "0")) >= 0
    if status == 200:
        return
    assert status in {400, 403, 428}
    payload = json.loads(body)
    assert payload["pow"]["code"] in _POW_REJECTIONS
    assert (status, payload["detail"]) == _POW_REJECTIONS[payload["pow"]["code"]]
    assert response_headers["x-pow-challenge"] == payload["pow"]["challenge"]
    assert response_headers["content-length"] == str(len(body))
    assert response_headers["cache-control"] == "no-store"


def _zero_bits(text: str) -> int:
    value = int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")
    return 256 - value.bit_length()


@given(method=methods, path=paths, query=queries, other_path=paths)
def test_a_valid_proof_is_accepted_once_and_only_for_its_request(
    method: str, path: str, query: bytes, other_path: str
) -> None:
    guard = _guard()
    ip = "198.51.100.4"
    challenge, bits, _ = guard._protection.pow.issue(ip)  # type: ignore[union-attr]
    query_part = f"?{query.decode('latin-1')}" if query else ""
    nonce = solve_pow(challenge, method, path + query_part, bits)
    proof = [(b"x-pow", f"{challenge}:{nonce}".encode())]

    def status(path: str, ip: str) -> int:
        return asyncio.run(_call(guard, _scope(method, path, query, proof, ip)))[0]

    if other_path != path:
        # Another request needs its own work, unless this nonce happens to be
        # enough for it as well (probability 2^-bits).
        work = _zero_bits(f"{challenge}\n{nonce}\n{method} {other_path}{query_part}")
        assert status(other_path, ip) == (200 if work >= bits else 403)
    assert status(path, "198.51.100.5") == 403
    assert status(path, ip) == 200
    assert status(path, ip) == 403  # replayed


@given(burst=st.integers(1, 30), extra=st.integers(1, 10))
def test_rate_limit_allows_exactly_the_burst(burst: int, extra: int) -> None:
    guard = _guard(enable_pow=False, rate_limit_rpm=burst)
    remaining = []
    for _ in range(burst + extra):
        status, headers, _ = asyncio.run(_call(guard, _scope("GET", "/v1/x", b"", [], "192.0.2.9")))
        if status == 429:
            assert int(headers["retry-after"]) >= 1
            assert headers["x-ratelimit-remaining"] == "0"
            remaining.append(-1)
        else:
            assert status == 200
            remaining.append(int(headers["x-ratelimit-remaining"]))
    assert remaining == [*range(burst - 1, -1, -1), *[-1] * extra]


FEED_CHANNELS = sorted(MANIFEST["channels"])


@pytest.fixture(scope="module")
def feed_client() -> Iterator[TestClient]:
    import time_machine  # noqa: PLC0415

    from tests.conftest import FROZEN_NOW, FixtureFetcher  # noqa: PLC0415

    with (
        time_machine.travel(FROZEN_NOW, tick=False),
        TestClient(create_app(make_settings(), fetcher=FixtureFetcher())) as client,
    ):
        yield client


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=25)
@given(channels=st.lists(st.sampled_from(FEED_CHANNELS), min_size=1, max_size=8))
def test_feed_holds_each_post_once_from_the_requested_channels(
    feed_client: TestClient, channels: list[str]
) -> None:
    response = feed_client.post("/v1/feed", json={"channels": channels})
    assert response.status_code == 200
    result = response.json()["result"]
    keys = [(item["channel"]["username"].lower(), item["id"]) for item in result]
    assert len(keys) == len(set(keys))
    assert {channel for channel, _ in keys} <= {c.lower() for c in channels}
    reordered = feed_client.post("/v1/feed", json={"channels": sorted(set(channels))[::-1]})
    assert sorted(keys) == sorted(
        (item["channel"]["username"].lower(), item["id"]) for item in reordered.json()["result"]
    )
