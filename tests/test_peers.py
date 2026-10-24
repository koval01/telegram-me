"""The peer layer: nodes on one network share their work without Redis.

Each app built here is one node (one granian worker of some server): its own
in-process cache, flights and upstream gate, and its own peer node on a
localhost port. They find each other through PEERS (or Redis markers) and
talk over real sockets.
"""

import asyncio
import json
import math
import secrets
import signal
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
import telegram_me_native
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi import FastAPI

from telegram_me import container as container_module
from telegram_me import heartbeat
from telegram_me import peers as peers_module
from telegram_me.api import protection
from telegram_me.app import create_app
from telegram_me.cache import KEY_PREFIX, MemoryCache, PeerCache, RedisCache
from telegram_me.peers import Peers, error_code, error_from, route
from telegram_me.services import popularity as popularity_module
from telegram_me.services.lease import PeerLease
from telegram_me.services.popularity import PeerPopularity
from telegram_me.telegram.errors import (
    OverloadedError,
    TelegramNotFoundError,
    TelegramUpstreamError,
)
from tests.conftest import FixtureFetcher, Work, make_settings
from tests.test_coalescing import ENDPOINTS, SlowFetcher, call, container, fire, settle
from tests.workload import CHANNELS

MISSING = "zzzzzznotexistingchannelzzzz"


def secret() -> str:
    return secrets.token_hex(32)


def peers_of(app: FastAPI) -> Peers:
    peers = container(app).peers
    assert peers is not None
    return peers


async def eventually(check: Callable[[], bool], within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def joined(apps: list[FastAPI]) -> None:
    """Wait until every node sees every other one."""
    await eventually(
        lambda: all(len(peers_of(app).node.members()) == len(apps) - 1 for app in apps)
    )
    # And the event loop has seen it too (ownership uses that view).
    await eventually(lambda: all(len(peers_of(app)._members) == len(apps) - 1 for app in apps))


def peer_settings(seeds: list[str], key: str, **overrides: Any) -> dict[str, Any]:
    return {
        "enable_peers": True,
        "peer_secret": key,
        "peer_port": 0,
        "peer_advertise_addr": "127.0.0.1",
        "peers": seeds,
        "peer_allow_cidr": ["127.0.0.0/8"],
        **overrides,
    }


@asynccontextmanager
async def nodes(
    count: int,
    fetcher: FixtureFetcher,
    monkeypatch: pytest.MonkeyPatch,
    *,
    redis: FakeServer | None = None,
    key: str | None = None,
    seeded: bool = True,
    **overrides: Any,
) -> AsyncIterator[list[FastAPI]]:
    """`count` running nodes, each seeded with the first one's address."""
    if redis is not None:
        monkeypatch.setattr(
            container_module,
            "create_redis",
            lambda _settings: FakeAsyncRedis(server=redis, decode_responses=True),
        )
    key = key or secret()
    apps: list[FastAPI] = []
    async with AsyncExitStack() as stack:
        for _ in range(count):
            seeds = [peers_of(apps[0]).addr] if apps and seeded else []
            app = create_app(
                make_settings(**peer_settings(seeds, key, **overrides)), fetcher=fetcher
            )
            await stack.enter_async_context(app.router.lifespan_context(app))
            apps.append(app)
        await joined(apps)
        yield apps


def owned_by(apps: list[FastAPI], index: int) -> str:
    """A channel of the fixtures whose keys node `index` owns."""
    addr = peers_of(apps[index]).addr
    for channel in CHANNELS:
        owners = {
            peers_of(app).owner(f"page:full:{channel}:0") or peers_of(app).addr for app in apps
        }
        if owners == {addr}:
            return channel
    pytest.fail("no fixture channel is owned by this node")


# Every key once across nodes ---------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("count", [2, 3])
async def test_identical_requests_on_all_nodes_fetch_once(
    endpoint: str, count: int, work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    async with nodes(count, fetcher, monkeypatch) as apps:
        replies = await fire(apps, [ENDPOINTS[endpoint]], rounds=30)
        await settle(apps)
        leases = [container(app).lease for app in apps]
    assert {reply.status for reply in replies} == {200}
    assert len({reply.body for reply in replies}) == 1
    assert len(fetcher.requests) == 1
    assert len(work.parsed) <= 1
    # The owner renders the body; the others get it along with the page.
    assert len(work.rendered) <= 1
    assert all(isinstance(lease, PeerLease) for lease in leases)


async def test_feed_across_nodes_fetches_every_channel_once(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    feed = ("POST", "/v1/feed", {"channels": list(CHANNELS)})
    bodies = [("GET", f"/v1/body/{channel}", None) for channel in CHANNELS]
    async with nodes(3, fetcher, monkeypatch) as apps:
        replies = await fire(apps, [feed, *bodies], rounds=4)
    assert {reply.status for reply in replies} == {200}
    assert sorted(path for _, path in fetcher.requests) == sorted(f"/s/{c}" for c in CHANNELS)


async def test_owner_answers_fill_the_askers_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = SlowFetcher()
    async with nodes(2, fetcher, monkeypatch) as apps:
        channel = owned_by(apps, 0)
        path = ("GET", f"/v1/body/{channel}", None)
        first = await call(apps[1], *path)
        asked = peers_of(apps[1]).node.stats()["asked"]
        again = await call(apps[1], *path)
        cache = container(apps[1]).cache
        assert isinstance(cache, PeerCache)
        copy = await cache.local.get(f"json:body:page:full:{channel}:0")
    assert (first.status, again.status, first.body) == (200, 200, again.body)
    # The second request was an in-process hit: nothing more was asked.
    assert peers_of(apps[1]).node.stats()["asked"] == asked
    assert copy is not None
    assert len(fetcher.requests) == 1


async def test_a_miss_is_answered_from_the_owners_cache(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    async with nodes(2, fetcher, monkeypatch) as apps:
        channel = owned_by(apps, 0)
        path = ("GET", f"/v1/body/{channel}", None)
        on_owner = await call(apps[0], *path)
        on_other = await call(apps[1], *path)
        served = peers_of(apps[0]).node.stats()
    assert (on_owner.status, on_other.body) == (200, on_owner.body)
    # One read of the owner's cache, no load, no second fetch or render.
    assert (served["served_gets"], served["served_loads"]) == (1, 0)
    assert (len(fetcher.requests), len(work.rendered)) == (1, 1)


async def test_owner_errors_are_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = SlowFetcher()
    async with nodes(2, fetcher, monkeypatch) as apps:
        replies = await fire(apps, [("GET", f"/v1/preview/{MISSING}", None)], rounds=10)
    assert {reply.status for reply in replies} == {404}
    assert len(fetcher.requests) == 1


async def test_nodes_without_peer_coalescing_fetch_once_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The duplicates the peer layer removes, with the same nodes unjoined."""
    fetcher = SlowFetcher()
    async with AsyncExitStack() as stack:
        apps = []
        for _ in range(2):
            app = create_app(make_settings(), fetcher=fetcher)
            await stack.enter_async_context(app.router.lifespan_context(app))
            apps.append(app)
        await fire(apps, [ENDPOINTS["body"]], rounds=10)
    assert len(fetcher.requests) == 2


# Failures never fail a request -------------------------------------------------------

_BARE_NODE = """
import sys, time
import telegram_me_native as native
node = native.PeerNode(
    cache=native.LocalCache(1 << 20), secret=sys.argv[1].encode(), ports=[0],
    bind="127.0.0.1", advertise="127.0.0.1", seeds=[sys.argv[2]], allow=["127.0.0.0/8"],
)
print(node.addr, flush=True)
time.sleep(120)
"""


@asynccontextmanager
async def bare_node(key: str, seed: str) -> AsyncIterator[tuple[asyncio.subprocess.Process, str]]:
    """Another process with a peer node whose Python never answers loads."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _BARE_NODE, key, seed, stdout=asyncio.subprocess.PIPE
    )
    try:
        assert process.stdout is not None
        addr = await process.stdout.readline()
        yield process, addr.decode().strip()
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def test_a_crashed_owner_is_not_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    key = secret()
    fetcher = SlowFetcher()
    async with nodes(1, fetcher, monkeypatch, key=key) as apps:
        peers = peers_of(apps[0])
        async with bare_node(key, peers.addr) as (process, addr):
            await eventually(lambda: peers._members == [addr])
            channel = next(c for c in CHANNELS if peers.owner(f"page:full:{c}:0") == addr)
            process.send_signal(signal.SIGKILL)
            await process.wait()
            started = time.monotonic()
            reply = await call(apps[0], "GET", f"/v1/body/{channel}")
            elapsed = time.monotonic() - started
    assert reply.status == 200
    assert elapsed < 1.0
    assert fetcher.requests == [("GET", f"/s/{channel}")]


async def test_a_stuck_owner_costs_one_wait_then_the_asker_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = secret()
    monkeypatch.setattr(container_module, "_LEASE_TTL", 0.2)
    fetcher = SlowFetcher()
    async with nodes(1, fetcher, monkeypatch, key=key, telegram_timeout=0.1) as apps:
        peers = peers_of(apps[0])
        async with bare_node(key, peers.addr) as (_process, addr):
            await eventually(lambda: peers._members == [addr])
            channel = next(c for c in CHANNELS if peers.owner(f"page:full:{c}:0") == addr)
            started = time.monotonic()
            reply = await call(apps[0], "GET", f"/v1/body/{channel}")
            elapsed = time.monotonic() - started
    assert reply.status == 200
    # It accepted the load and never answered: the asker waited its wait out.
    assert 0.3 < elapsed < 2.0
    assert fetcher.requests == [("GET", f"/s/{channel}")]


async def test_nodes_with_another_secret_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        peers_module.logger, "warning", lambda message, *args: warnings.append(message % args)
    )
    fetcher = SlowFetcher()
    async with AsyncExitStack() as stack:
        first = create_app(make_settings(**peer_settings([], secret())), fetcher=fetcher)
        await stack.enter_async_context(first.router.lifespan_context(first))
        seed = [peers_of(first).addr]
        for encryption in (False, True):
            settings = peer_settings(seed, secret(), peer_encryption=encryption)
            stranger = create_app(make_settings(**settings), fetcher=fetcher)
            await stack.enter_async_context(stranger.router.lifespan_context(stranger))
        await eventually(lambda: peers_of(first).node.stats()["rejected"] >= 2)
        peers_of(first).report()
        peers_of(first).report()  # nothing new since
        replies = [await call(app, *ENDPOINTS["body"]) for app in (first, stranger)]
        assert peers_of(first).node.members() == []
    assert [reply.status for reply in replies] == [200, 200]
    # Reported once (the second report had nothing new), with the likely cause.
    rejected = [warning for warning in warnings if "rejected" in warning]
    assert len(rejected) == 1
    assert "PEER_SECRET" in rejected[0]
    # Each did the work on its own.
    assert len(fetcher.requests) == 2


async def test_encrypted_nodes_work_together(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = SlowFetcher()
    async with nodes(2, fetcher, monkeypatch, peer_encryption=True) as apps:
        replies = await fire(apps, [ENDPOINTS["body"]], rounds=10)
    assert {reply.status for reply in replies} == {200}
    assert len(fetcher.requests) == 1


@asynccontextmanager
async def two_peers(**overrides: Any) -> AsyncIterator[tuple[Peers, Peers]]:
    """Two bare nodes (no app) that know each other."""
    key = secret()
    first = Peers.start(
        make_settings(**peer_settings([], key, **overrides)), MemoryCache(1 << 20), refresher=True
    )
    assert first is not None
    second = Peers.start(
        make_settings(**peer_settings([first.addr], key, **overrides)),
        MemoryCache(1 << 20),
        refresher=True,
    )
    assert second is not None
    async with first.running(), second.running():
        await eventually(lambda: bool(first._members and second._members))
        yield first, second


async def test_loads_end_with_the_owners_error_or_unavailable() -> None:
    async with two_peers() as (owner, asker):
        assert (await asker.load(owner.addr, "preview:x", -math.inf, 5)).unavailable

        async def serve(key: str, newer_than: float) -> list[str]:
            if key == "preview:gone":
                raise TelegramNotFoundError("gone")
            if key == "preview:busy":
                raise OverloadedError
            raise ValueError(key)

        owner.serve_with(serve)
        gone = await asker.load(owner.addr, "preview:gone", -math.inf, 5)
        busy = await asker.load(owner.addr, "preview:busy", -math.inf, 5)
        odd = await asker.load(owner.addr, "preview:odd", -math.inf, 5)
    assert isinstance(gone.error, TelegramNotFoundError)
    assert (busy.unavailable, odd.unavailable) == (True, True)


async def test_casts_reach_handlers_and_need_peers() -> None:
    received: list[bytes] = []
    async with two_peers() as (first, second):
        second.on_cast("topic", received.append)
        first.cast("topic", b"data")
        first.cast("unknown", b"ignored")
        await eventually(lambda: received == [b"data"])
    alone = Peers.start(
        make_settings(**peer_settings([], secret())), MemoryCache(1 << 20), refresher=True
    )
    assert alone is not None
    async with alone.running():
        alone.cast("topic", b"to nobody")
        assert alone.owner("page:full:durov:0") is None


def test_a_node_without_a_free_port_runs_without_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(**_kwargs: Any) -> None:
        raise OSError("no peer port to try")

    monkeypatch.setattr(telegram_me_native, "PeerNode", busy)
    settings = make_settings(**peer_settings([], secret()))
    assert Peers.start(settings, MemoryCache(1 << 20), refresher=True) is None
    assert Peers.start(make_settings(), MemoryCache(1 << 20), refresher=True) is None


async def test_redis_markers_without_a_usable_peer_are_skipped() -> None:
    redis = FakeAsyncRedis(decode_responses=True)
    shared = RedisCache(redis)
    pattern = f"{KEY_PREFIX}node:*"
    assert await peers_module._peer_addresses(shared, pattern) == set()
    for name, marker in {
        "a": '{"peer": "10.0.0.108:7946"}',
        "b": "not json",
        "c": '{"peer": 7946}',
        "d": "[1, 2]",
        "e": '{"version": "2.0.0"}',
    }.items():
        await redis.set(f"{KEY_PREFIX}node:{name}", marker)
    assert await peers_module._peer_addresses(shared, pattern) == {"10.0.0.108:7946"}
    shared.trip(OSError("down"))
    shared.trip(OSError("down"))
    shared.trip(OSError("down"))
    assert await peers_module._peer_addresses(shared, pattern) == set()


async def test_odd_popularity_messages_are_ignored() -> None:
    async with two_peers() as (first, _second):
        popularity = PeerPopularity(first, 3600)
        popularity._received(b"durov\t2\nbad\tx\nneg\t-1\n\t3\ninf\tinf\ntelegram\t1.5")
        await popularity.decay(0.1)  # a no-op: each node decays on its own clock
        assert dict(await popularity.top(10)) == {"durov": 2.0, "telegram": 1.5}
        popularity.flush()  # nothing pending


# Coordination without Redis ----------------------------------------------------------


async def test_exactly_one_node_leads_the_refresher(monkeypatch: pytest.MonkeyPatch) -> None:
    async with nodes(3, FixtureFetcher(), monkeypatch, feed_refresh_mode="embedded") as apps:
        leaders = [await container(app).leader_lock.acquire() for app in apps]
        lowest = min(range(3), key=lambda i: tuple(map(int, peers_of(apps[i]).addr.split(":")[1:])))
    assert leaders.count(True) == 1
    assert leaders[lowest]


async def test_api_nodes_leave_leadership_to_the_feed_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with nodes(2, FixtureFetcher(), monkeypatch, feed_refresh_mode="worker") as apps:
        leaders = [await container(app).leader_lock.acquire() for app in apps]
    assert leaders == [False, False]


async def test_rate_limits_add_up_across_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(protection, "_SYNC_INTERVAL", 0.02)
    limits: dict[str, Any] = {"enable_rate_limit": True, "rate_limit_rpm": 6, "rate_limit_burst": 6}
    async with nodes(2, FixtureFetcher(), monkeypatch, **limits) as apps:
        first = [await call(app, "GET", "/healthz") for app in apps for _ in range(4)]
        await asyncio.sleep(0.3)
        after = [await call(app, "GET", "/healthz") for app in apps]
    # 4 + 4 requests of one client: within the limit on each node, not overall.
    assert {reply.status for reply in first} == {200}
    assert [reply.status for reply in after] == [429, 429]


async def test_feed_popularity_is_counted_on_every_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(popularity_module, "_HITS_INTERVAL", 0.02)
    async with nodes(2, SlowFetcher(), monkeypatch) as apps:
        reply = await call(apps[0], "POST", "/v1/feed", {"channels": ["durov", "Telegram"]})
        popularity = container(apps[1]).popularity
        assert isinstance(popularity, PeerPopularity)
        await eventually(lambda: bool(popularity._scores))
        top = dict(await popularity.top(10))
    assert reply.status == 200
    assert top == {"durov": 1.0, "telegram": 1.0}


# With Redis --------------------------------------------------------------------------


async def test_with_redis_the_owner_fetches_without_a_lease(
    work: Work, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SlowFetcher()
    redis = FakeServer()
    async with nodes(2, fetcher, monkeypatch, redis=redis) as apps:
        replies = await fire(apps, [ENDPOINTS["body"]], rounds=20)
        await settle(apps)
        leases = [container(app).lease for app in apps]
        stored = await FakeAsyncRedis(server=redis).exists(f"{KEY_PREFIX}page:full:durov:0")
    assert {reply.status for reply in replies} == {200}
    assert len(fetcher.requests) == 1
    assert all(isinstance(lease, PeerLease) for lease in leases)
    # Nobody fell back to the Redis lease.
    assert [lease.stats["fallback"] for lease in leases if isinstance(lease, PeerLease)] == [0, 0]
    # The owner still writes Redis, for nodes without peers (or restarting ones).
    assert stored == 1


async def test_nodes_find_each_other_through_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    names = iter(range(100))
    monkeypatch.setattr(heartbeat, "node_id", lambda: f"host:{next(names)}")
    monkeypatch.setattr(peers_module, "_DISCOVERY_INTERVAL", 0.05)
    redis = FakeServer()
    async with nodes(2, FixtureFetcher(), monkeypatch, redis=redis, seeded=False) as apps:
        client = FakeAsyncRedis(server=redis)
        keys = [key async for key in client.scan_iter(match=f"{KEY_PREFIX}node:*")]
        markers = [json.loads(marker) for marker in await client.mget(keys) if marker]
    assert sorted(marker["peer"] for marker in markers) == sorted(
        peers_of(app).addr for app in apps
    )


async def test_peers_are_off_by_default(client: Any) -> None:
    assert client.app.state.container.peers is None


# Pieces ------------------------------------------------------------------------------


def test_all_keys_of_a_channel_have_one_route() -> None:
    keys = [
        "page:full:durov:0",
        "page:simplified:durov:531",
        "json:body:page:full:durov:0",
        "json:post:full:durov:531",
        "more:full:durov:before:531",
        "preview:durov",
    ]
    assert {route(key) for key in keys} == {"durov"}
    assert route("something:else") == "something:else"


def test_errors_travel_by_class() -> None:
    missing = TelegramNotFoundError("gone")
    assert isinstance(error_from(error_code(missing), "gone"), TelegramNotFoundError)
    assert isinstance(
        error_from(error_code(TelegramUpstreamError("x")), "x"), TelegramUpstreamError
    )
    # A local overload, or anything unexpected: the asker does the work.
    assert error_code(OverloadedError()) == 0
    assert error_code(ValueError()) == 0
    assert error_from(0, "") is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"enable_peers": True}, "PEER_SECRET"),
        ({"enable_peers": True, "peer_secret": "short"}, "PEER_SECRET"),
        ({"peer_allow_cidr": "10.0.0.0/33"}, "network"),
        ({"peer_advertise_addr": "tme-1"}, "IP"),
    ],
)
def test_bad_peer_settings_are_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_settings(**overrides)


def test_peer_lists_are_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERS", "10.0.0.108, tme-2.internal:7950,")
    monkeypatch.setenv("PEER_ALLOW_CIDR", "10.0.0.0/16")
    settings = make_settings(peer_port=7946, web_concurrency=2)
    assert settings.peers == ["10.0.0.108", "tme-2.internal:7950"]
    assert settings.peer_allow_cidr == ["10.0.0.0/16"]
    assert settings.peer_ports == [7946, 7947, 7948]
