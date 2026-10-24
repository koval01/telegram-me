"""Identical concurrent requests do the work once: in one process, across
processes and servers sharing Redis, and between the APIs and endpoints that
need the same page.

Each app built here stands for one granian worker (its own in-process cache,
single flights and upstream gate). Apps that share a `FakeServer` stand for
workers of any servers that share one Redis.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, MutableMapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, override

import pydantic_core
import pytest
import telegram_me_native as native
import time_machine
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi import FastAPI

from telegram_me import container as container_module
from telegram_me.app import create_app
from telegram_me.container import Container
from telegram_me.services import channels as channels_module
from telegram_me.services import feed as feed_module
from telegram_me.services.lease import RedisLease
from telegram_me.telegram.client import FetchResult, Method
from tests.conftest import FROZEN_NOW, MANIFEST, FixtureFetcher, make_settings
from tests.workload import CHANNELS, AliasFetcher

CHANNEL = "durov"
POST_ID: int = MANIFEST["channels"][CHANNEL]["post_id"]
DELAY = 0.05


class SlowFetcher(FixtureFetcher):
    """Recorded responses after a delay, so that concurrent requests overlap."""

    def __init__(self, delay: float = DELAY) -> None:
        super().__init__()
        self.delay = delay

    @override
    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        await asyncio.sleep(self.delay)
        return await super().fetch(method, path, query=query, headers=headers)


class SlowAliasFetcher(AliasFetcher):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    @override
    async def fetch(self, method: Method, path: str, **kwargs: Any) -> FetchResult:
        self.paths.append(path)
        await asyncio.sleep(DELAY)
        return await super().fetch(method, path, **kwargs)


@dataclass
class Work:
    """CPU-heavy steps performed, counted across all apps of a test."""

    parsed: list[str] = field(default_factory=list)
    rendered: list[int] = field(default_factory=list)
    ranked: list[int] = field(default_factory=list)


@pytest.fixture
def work(monkeypatch: pytest.MonkeyPatch) -> Work:
    counts = Work()
    process_page = channels_module._process_page
    render_body = channels_module._render_body
    build_feed = native.build_feed

    def counting_process_page(html: str, channel: str, **kwargs: Any) -> Any:
        counts.parsed.append(channel)
        return process_page(html, channel, **kwargs)

    def counting_render_body(data: Any) -> bytes:
        counts.rendered.append(1)
        return render_body(data)

    def counting_build_feed(*args: Any) -> Any:
        counts.ranked.append(1)
        return build_feed(*args)

    monkeypatch.setattr(channels_module, "_process_page", counting_process_page)
    monkeypatch.setattr(channels_module, "_render_body", counting_render_body)
    monkeypatch.setattr(native, "build_feed", counting_build_feed)
    return counts


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


async def call(app: FastAPI, method: str, path: str, body: Any = None) -> Reply:
    """One request straight to the ASGI app."""
    raw_path, _, query = path.partition("?")
    payload = json.dumps(body).encode() if body is not None else b""
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    reply = Reply(0, {}, b"")
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            reply.status = message["status"]
            reply.headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            reply.body += message.get("body", b"")

    await app(scope, receive, send)
    return reply


@asynccontextmanager
async def servers(
    count: int,
    fetcher: FixtureFetcher,
    monkeypatch: pytest.MonkeyPatch,
    *,
    redis: bool = True,
    **overrides: Any,
) -> AsyncIterator[list[FastAPI]]:
    """`count` running apps; with `redis`, they share one (fake) Redis."""
    server = FakeServer()
    if redis:
        monkeypatch.setattr(
            container_module,
            "create_redis",
            lambda _settings: FakeAsyncRedis(server=server, decode_responses=True),
        )
    apps = [create_app(make_settings(**overrides), fetcher=fetcher) for _ in range(count)]
    async with AsyncExitStack() as stack:
        for app in apps:
            await stack.enter_async_context(app.router.lifespan_context(app))
        yield apps


def container(app: FastAPI) -> Container:
    result: Container = app.state.container
    return result


async def settle(apps: list[FastAPI]) -> None:
    """Let background refreshes and lease releases finish."""
    for _ in range(50):
        await asyncio.sleep(DELAY / 5)
        busy = [app for app in apps if container(app).client.gate.active]
        if not busy:
            break
    for app in apps:
        lease = container(app).lease
        if isinstance(lease, RedisLease):
            await lease.drain()


async def fire(
    apps: list[FastAPI], requests: list[tuple[str, str, Any]], *, rounds: int = 1
) -> list[Reply]:
    """Send every request `rounds` times at once, spread round-robin over `apps`."""
    batch = [request for _ in range(rounds) for request in requests]
    return await asyncio.gather(
        *(call(apps[i % len(apps)], *request) for i, request in enumerate(batch))
    )


ENDPOINTS: dict[str, tuple[str, str, Any]] = {
    "body": ("GET", f"/v1/body/{CHANNEL}", None),
    "body_position": ("GET", f"/v1/body/{CHANNEL}?position={POST_ID}", None),
    "post": ("GET", f"/v1/post/{CHANNEL}/{POST_ID}", None),
    "more": ("GET", f"/v1/more/{CHANNEL}/before/{POST_ID}", None),
    "preview": ("GET", f"/v1/preview/{CHANNEL}", None),
}


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("count", [1, 2], ids=["one-process", "two-servers"])
async def test_identical_requests_fetch_and_render_once(
    endpoint: str, count: int, work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    async with servers(count, fetcher, monkeypatch) as apps:
        replies = await fire(apps, [ENDPOINTS[endpoint]], rounds=40)
        await settle(apps)
    assert {reply.status for reply in replies} == {200}
    assert len({reply.body for reply in replies}) == 1
    assert len(fetcher.requests) == 1
    assert len(work.parsed) <= 1
    assert len(work.rendered) <= 1


async def test_workers_without_shared_coalescing_fetch_once_each(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The duplicates the lease removes: one fetch per process (the old behaviour)."""
    fetcher = SlowFetcher()
    async with servers(2, fetcher, monkeypatch, shared_coalescing=False) as apps:
        await fire(apps, [ENDPOINTS["body"]], rounds=20)
    assert len(fetcher.requests) == 2
    assert len(work.parsed) == 2


@pytest.mark.parametrize("count", [1, 2], ids=["one-process", "two-servers"])
async def test_rest_and_graphql_share_fetch_and_render(
    count: int, work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = {"query": f'{{ body(channel: "{CHANNEL.upper()}") {{ channel {{ username }} }} }}'}
    fetcher = SlowFetcher()
    async with servers(count, fetcher, monkeypatch) as apps:
        replies = await fire(apps, [ENDPOINTS["body"], ("POST", "/graphql", query)], rounds=20)
    assert {reply.status for reply in replies} == {200}
    assert len(fetcher.requests) == 1
    assert len(work.rendered) == 1


async def test_post_and_body_around_one_position_share_the_page(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    async with servers(2, fetcher, monkeypatch) as apps:
        requests = [
            ENDPOINTS["post"],
            ENDPOINTS["body_position"],
            ("GET", f"/v1/body/{CHANNEL.upper()}?position={POST_ID}", None),
        ]
        replies = await fire(apps, requests, rounds=10)
    assert {reply.status for reply in replies} == {200}
    assert fetcher.requests == [("GET", f"/s/{CHANNEL}/{POST_ID}")]


async def test_feed_and_channel_requests_share_page_fetches(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    feed = ("POST", "/v1/feed", {"channels": list(CHANNELS)})
    bodies = [("GET", f"/v1/body/{channel}", None) for channel in CHANNELS]
    async with servers(2, fetcher, monkeypatch) as apps:
        replies = await fire(apps, [feed, *bodies], rounds=5)
    assert {reply.status for reply in replies} == {200}
    assert sorted(path for _, path in fetcher.requests) == sorted(f"/s/{c}" for c in CHANNELS)
    assert len(work.parsed) == len(CHANNELS)


async def test_identical_feeds_are_ranked_and_rendered_once(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    channels = list(CHANNELS)
    shouting = [channel.upper() for channel in channels]
    query = {"query": f"{{ feed(channels: {json.dumps(channels)}) {{ id }} }}"}
    decoded: list[int] = []
    from_json = pydantic_core.from_json

    def counting_from_json(data: Any) -> Any:
        decoded.append(1)
        return from_json(data)

    monkeypatch.setattr(feed_module, "from_json", counting_from_json)
    async with servers(1, fetcher, monkeypatch, redis=False) as apps:
        replies = await fire(
            apps,
            [
                ("POST", "/v1/feed", {"channels": channels}),
                ("POST", "/v1/feed", {"channels": [*shouting, channels[0]]}),
                ("POST", "/graphql", query),
            ],
            rounds=10,
        )
    assert {reply.status for reply in replies} == {200}
    assert len(work.ranked) == 1
    assert len(decoded) == 1  # for GraphQL, also once
    rest = [reply.body for reply in replies if b'"result"' in reply.body]
    assert len(set(rest)) == 1


async def test_refresher_joins_user_fetches_and_skips_fresh_pages(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    with time_machine.travel(FROZEN_NOW, tick=False) as traveller:
        async with servers(2, fetcher, monkeypatch) as (first, second):
            await call(first, "POST", "/v1/feed", {"channels": [CHANNEL]})
            await settle([first, second])
            traveller.shift(31)  # stale for /v1/body, due for the refresher
            fetcher.requests.clear()
            # A user on one server and the refresher on another want the page.
            user, refreshed = await asyncio.gather(
                call(first, *ENDPOINTS["body"]), container(second).refresher().tick()
            )
            await settle([first, second])
            assert (user.status, refreshed) == (200, 1)
            assert fetcher.requests == [("GET", f"/s/{CHANNEL}")]

            # A page some process fetched moments ago is not fetched again.
            traveller.shift(1)
            fetcher.requests.clear()
            assert await container(second).refresher().tick() == 1
            await settle([first, second])
    assert fetcher.requests == []


@pytest.mark.parametrize("count", [1, 2], ids=["one-process", "two-servers"])
async def test_miss_storm_after_expiry_is_one_fetch(
    count: int, work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    with time_machine.travel(FROZEN_NOW, tick=False) as traveller:
        async with servers(count, fetcher, monkeypatch, early_refresh_window=0) as apps:
            await fire(apps, list(ENDPOINTS.values()))
            await settle(apps)
            assert len(fetcher.requests) == len(ENDPOINTS) - 1  # body + post share a page
            fetcher.requests.clear()
            traveller.shift(11)  # past CACHE_MODE=normal's 10 s
            replies = await fire(apps, list(ENDPOINTS.values())[:-1], rounds=20)
            await settle(apps)
    assert {reply.status for reply in replies} == {200}
    # body, body?position (shared with post) and more expired; previews live for an hour.
    assert sorted(path for _, path in fetcher.requests) == sorted(
        [f"/s/{CHANNEL}", f"/s/{CHANNEL}/{POST_ID}", f"/s/{CHANNEL}"]
    )


async def test_hot_entry_is_renewed_early_by_one_request(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    with time_machine.travel(FROZEN_NOW, tick=False) as traveller:
        async with servers(2, fetcher, monkeypatch, early_refresh_window=0.1) as apps:
            await call(apps[0], *ENDPOINTS["body"])
            await settle(apps)
            traveller.shift(9.999)  # past any renewal point, before the page TTL runs out
            replies = await fire(apps, [ENDPOINTS["body"]], rounds=60)
            await settle(apps)
            # Every request was answered from the cache; one renewed the page.
            assert {reply.status for reply in replies} == {200}
            assert len(fetcher.requests) == 2
            traveller.shift(0.5)  # past the original expiry: still no miss
            await fire(apps, [ENDPOINTS["body"]], rounds=20)
            await settle(apps)
    assert len(fetcher.requests) == 2


async def test_abuse_keeps_memory_and_work_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many distinct keys and many identical ones at once, against small limits."""
    fetcher = SlowAliasFetcher()
    limits: dict[str, Any] = {"telegram_max_concurrency": 2, "upstream_queue_limit": 8}
    limits |= {"max_waiting_requests": 300}
    distinct = [("GET", f"/v1/body/durov_m{i}", None) for i in range(400)]
    identical = [ENDPOINTS["body"]] * 400
    async with servers(1, fetcher, monkeypatch, **limits) as (app,):
        services = container(app)
        gate = services.client.gate
        table = services.channels._pages._table
        peaks = {"active": 0, "queued": 0, "keys": 0, "waiters": 0}
        done = asyncio.Event()

        async def sample() -> None:
            while not done.is_set():
                peaks["active"] = max(peaks["active"], gate.active)
                peaks["queued"] = max(peaks["queued"], gate.queued)
                peaks["keys"] = max(peaks["keys"], len(table))
                peaks["waiters"] = max(peaks["waiters"], table.waiters)
                await asyncio.sleep(0)

        sampler = asyncio.ensure_future(sample())
        mixed = [request for pair in zip(distinct, identical, strict=True) for request in pair]
        replies = await asyncio.gather(*(call(app, *request) for request in mixed))
        done.set()
        await sampler
        await settle([app])
        assert (gate.active, gate.queued, len(table), table.waiters) == (0, 0, 0, 0)

    statuses = [reply.status for reply in replies]
    assert set(statuses) <= {200, 503}
    shed = [reply for reply in replies if reply.status == 503]
    assert shed, "the burst should exceed the limits"
    assert all(int(reply.headers["retry-after"]) >= 1 for reply in shed)
    # Upstream work is bounded by what was admitted, never by what was asked.
    assert peaks["active"] <= 2
    assert peaks["queued"] <= 8
    assert peaks["waiters"] <= 300
    served = statuses.count(200)
    assert fetcher.count <= served
    # The identical requests shared one fetch (or were shed together).
    assert fetcher.paths.count(f"/s/{CHANNEL}") <= 1
    assert len(fetcher.paths) == len(set(fetcher.paths))
