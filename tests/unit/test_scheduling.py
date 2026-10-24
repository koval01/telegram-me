"""The scheduling pieces on their own: fair upstream gate, bounded single
flights, and the shared lease in Redis."""

import asyncio
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from telegram_me.cache import KEY_PREFIX, CacheEntry, MemoryCache, RedisCache
from telegram_me.services import upstream
from telegram_me.services.coalesce import SingleFlight, flight_table
from telegram_me.services.lease import LEASE_PREFIX, RedisLease
from telegram_me.services.upstream import FairGate
from telegram_me.telegram.errors import (
    OverloadedError,
    TelegramNotFoundError,
    TelegramUpstreamError,
)

# Fair gate -------------------------------------------------------------------------


async def _hold(gate: FairGate, flow: str, log: list[str], release: asyncio.Event) -> None:
    async with gate.slot(flow):
        log.append(flow)
        await release.wait()


async def test_gate_serves_flows_round_robin() -> None:
    gate = FairGate(1)
    log: list[str] = []
    release = asyncio.Event()
    holder = asyncio.ensure_future(_hold(gate, "first", log, release))
    await asyncio.sleep(0)
    feed = [asyncio.ensure_future(_quick(gate, "feed", log)) for _ in range(3)]
    await asyncio.sleep(0)
    user = asyncio.ensure_future(_quick(gate, "durov", log))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(holder, *feed, user)
    assert log == ["first", "feed", "durov", "feed", "feed"]


async def _quick(gate: FairGate, flow: str, log: list[str]) -> None:
    async with gate.slot(flow):
        log.append(flow)
        await asyncio.sleep(0)


async def test_gate_flow_comes_from_the_job() -> None:
    gate = FairGate(1)
    log: list[str] = []
    release = asyncio.Event()
    holder = asyncio.ensure_future(_hold(gate, "a", log, release))
    await asyncio.sleep(0)

    async def feed() -> None:
        with upstream.flow("feed:1"):
            await asyncio.gather(*(_quick(gate, f"channel{i}", log) for i in range(3)))

    fanout = asyncio.ensure_future(feed())
    await asyncio.sleep(0)
    user = asyncio.ensure_future(_quick(gate, "b", log))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(holder, fanout, user)
    # The three channels of the feed count as one flow: the user goes second.
    assert log[:3] == ["a", "channel0", "b"]


async def test_background_work_waits_for_users_and_can_be_promoted() -> None:
    gate = FairGate(2)  # background work may hold one slot
    log: list[str] = []
    release_user, release_background = asyncio.Event(), asyncio.Event()

    async def background(name: str, job: upstream.UpstreamJob, release: asyncio.Event) -> None:
        upstream.set_job(job)
        await _hold(gate, name, log, release)

    holder = asyncio.ensure_future(
        background("held", upstream.UpstreamJob(background=True), release_background)
    )
    user = asyncio.ensure_future(_hold(gate, "user", log, release_user))
    await asyncio.sleep(0)
    job = upstream.UpstreamJob(background=True)
    done = asyncio.Event()
    done.set()
    refresh = asyncio.ensure_future(background("refresh", job, done))
    await asyncio.sleep(0)
    release_user.set()
    await user
    # A free slot, but background work is at its share: the refresh waits...
    await asyncio.sleep(0.01)
    assert log == ["held", "user"]
    assert gate.queued == 1
    # ...until a user needs its result.
    job.promote()
    await asyncio.wait_for(refresh, 1)
    assert log == ["held", "user", "refresh"]
    release_background.set()
    await holder


async def test_cancelled_waiter_gives_its_place_back() -> None:
    gate = FairGate(1)
    log: list[str] = []
    release = asyncio.Event()
    holder = asyncio.ensure_future(_hold(gate, "a", log, release))
    await asyncio.sleep(0)
    gone = asyncio.ensure_future(_quick(gate, "gone", log))
    stays = asyncio.ensure_future(_quick(gate, "stays", log))
    await asyncio.sleep(0)
    gone.cancel()
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(holder, stays)
    assert log == ["a", "stays"]
    assert (gate.active, gate.queued) == (0, 0)


async def test_full_gate_queue_sheds_with_retry_after() -> None:
    gate = FairGate(1, max_queue=2)
    release = asyncio.Event()
    log: list[str] = []
    tasks = [asyncio.ensure_future(_hold(gate, f"f{i}", log, release)) for i in range(3)]
    await asyncio.sleep(0)
    with pytest.raises(OverloadedError) as raised:
        async with gate.slot("late"):
            pass
    assert raised.value.retry_after >= 1
    release.set()
    await asyncio.gather(*tasks)


# Single flight ---------------------------------------------------------------------


async def test_waiters_beyond_the_bound_are_shed() -> None:
    table = flight_table(max_keys=10, max_waiters=3)
    flight: SingleFlight[int] = SingleFlight(table)
    release = asyncio.Event()

    async def work() -> int:
        await release.wait()
        return 1

    waiting = [asyncio.ensure_future(flight.run("k", work)) for _ in range(3)]
    await asyncio.sleep(0)
    with pytest.raises(OverloadedError):
        await flight.run("k", work)
    release.set()
    assert await asyncio.gather(*waiting) == [1, 1, 1]
    assert table.waiters == 0


async def test_flights_sharing_a_table_share_its_key_bound() -> None:
    table = flight_table(max_keys=2, max_waiters=100)
    pages: SingleFlight[int] = SingleFlight(table, "page|")
    bodies: SingleFlight[int] = SingleFlight(table, "body|")
    release = asyncio.Event()

    async def work() -> int:
        await release.wait()
        return 1

    first = asyncio.ensure_future(pages.run("k", work))
    second = asyncio.ensure_future(bodies.run("k", work))
    await asyncio.sleep(0)
    with pytest.raises(OverloadedError):
        await pages.run("other", work)
    assert pages.start("other", work) is None  # background work is dropped
    release.set()
    assert list(await asyncio.gather(first, second)) == [1, 1]
    assert len(table) == 0


async def test_user_joining_background_flight_promotes_it() -> None:
    flight: SingleFlight[str] = SingleFlight()
    release = asyncio.Event()
    seen: list[bool] = []

    async def work() -> str:
        await release.wait()
        seen.append(upstream.current_job().background)
        return "done"

    task = flight.start("k", work, background=True)
    assert task is not None
    await asyncio.sleep(0)
    user = asyncio.ensure_future(flight.run("k", work))
    await asyncio.sleep(0)
    release.set()
    assert await user == "done"
    assert seen == [False]


async def test_flights_inherit_the_starters_flow_but_not_its_job() -> None:
    flight: SingleFlight[upstream.UpstreamJob] = SingleFlight()

    async def work() -> upstream.UpstreamJob:
        return upstream.current_job()

    with upstream.flow("feed:7"):
        job = await flight.run("k", work)
    assert job.flow == "feed:7"
    assert job is not upstream.current_job()


# Lease -----------------------------------------------------------------------------


@pytest.fixture
def shared() -> FakeServer:
    return FakeServer()


def _lease(server: FakeServer, ttl: float = 2.0) -> tuple[RedisLease, RedisCache, MemoryCache]:
    cache = RedisCache(FakeAsyncRedis(server=server, decode_responses=True))
    local = MemoryCache(1 << 20)
    lease = RedisLease(cache, ttl=ttl, max_wait=ttl, local=local, max_local_ttl=5.0)
    return lease, cache, local


class Upstream:
    """A fetch that writes its result to the shared cache, like the services do."""

    def __init__(self, cache: RedisCache, value: bytes = b"page", delay: float = 0.05) -> None:
        self.cache = cache
        self.value = value
        self.delay = delay
        self.calls = 0
        self.error: Exception | None = None

    async def __call__(self) -> bytes:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        await self.cache.set("k", self.value, 60)
        return self.value


def _value(entry: CacheEntry) -> bytes:
    return entry.value


def _always(_entry: CacheEntry) -> bool:
    return True


async def test_lease_lets_one_process_fetch(shared: FakeServer) -> None:
    leases = [_lease(shared) for _ in range(4)]
    fetches = [Upstream(cache) for _, cache, _ in leases]
    results = await asyncio.gather(
        *(
            lease.run("k", fetch, _value, _always)
            for (lease, _, _), fetch in zip(leases, fetches, strict=True)
        )
    )
    assert results == [b"page"] * 4
    assert sum(fetch.calls for fetch in fetches) == 1
    # Waiters keep the value they picked up in their in-process cache.
    picked = [local for (lease, _, local), f in zip(leases, fetches, strict=True) if not f.calls]
    assert [await local.get("k") is not None for local in picked] == [True] * 3
    for lease, cache, _ in leases:
        await lease.drain()
        await cache.close()


async def test_fresh_shared_value_is_used_without_fetching(shared: FakeServer) -> None:
    lease, cache, _ = _lease(shared)
    await cache.set("k", b"cached", 60)
    fetch = Upstream(cache)
    assert await lease.run("k", fetch, _value, _always) == b"cached"
    assert fetch.calls == 0
    await lease.drain()
    assert await cache.client.exists(LEASE_PREFIX + "k") == 0


async def test_stale_shared_value_is_fetched_again(shared: FakeServer) -> None:
    lease, cache, _ = _lease(shared)
    await cache.set("k", b"old", 60)
    fetch = Upstream(cache, b"new")
    result = await lease.run("k", fetch, _value, lambda entry: entry.value != b"old")
    assert (result, fetch.calls) == (b"new", 1)


async def test_holder_error_is_shared_with_waiters(shared: FakeServer) -> None:
    (holder, cache, _), (waiter, other, _) = _lease(shared), _lease(shared)
    failing = Upstream(cache)
    failing.error = TelegramNotFoundError("Requested Telegram resource was not found")
    waiting = Upstream(other)
    results = await asyncio.gather(
        holder.run("k", failing, _value, _always),
        waiter.run("k", waiting, _value, _always),
        return_exceptions=True,
    )
    assert all(isinstance(result, TelegramNotFoundError) for result in results)
    assert (failing.calls, waiting.calls) == (1, 0)
    # Late arrivals get the same answer for a moment, without fetching.
    await holder.drain()
    with pytest.raises(TelegramNotFoundError):
        await waiter.run("k", waiting, _value, _always)
    assert waiting.calls == 0


async def test_waiter_takes_over_when_holder_fails_unexpectedly(shared: FakeServer) -> None:
    (holder, cache, _), (waiter, other, _) = _lease(shared), _lease(shared)
    failing = Upstream(cache)
    failing.error = RuntimeError("bug")
    waiting = Upstream(other, b"second")
    results = await asyncio.gather(
        holder.run("k", failing, _value, _always),
        waiter.run("k", waiting, _value, _always),
        return_exceptions=True,
    )
    assert isinstance(results[0], RuntimeError)
    assert results[1] == b"second"
    assert waiting.calls == 1


async def test_local_overload_is_not_shared(shared: FakeServer) -> None:
    (holder, cache, _), (waiter, other, _) = _lease(shared), _lease(shared)
    shed = Upstream(cache)
    shed.error = OverloadedError()
    waiting = Upstream(other)
    results = await asyncio.gather(
        holder.run("k", shed, _value, _always),
        waiter.run("k", waiting, _value, _always),
        return_exceptions=True,
    )
    assert isinstance(results[0], OverloadedError)
    assert results[1] == b"page"


async def test_waiter_stops_waiting_when_the_lease_runs_out(shared: FakeServer) -> None:
    lease, cache, _ = _lease(shared, ttl=0.3)
    # A holder that vanished without releasing (its lease lives longer here).
    await cache.client.set(LEASE_PREFIX + "k", "someone", px=10_000)
    fetch = Upstream(cache)
    started = asyncio.get_running_loop().time()
    assert await lease.run("k", fetch, _value, _always) == b"page"
    assert asyncio.get_running_loop().time() - started < 1.0
    assert (fetch.calls, lease.stats["fallback"]) == (1, 1)


async def test_holder_extends_its_lease_while_fetching(shared: FakeServer) -> None:
    (holder, cache, _), (waiter, other, _) = _lease(shared, ttl=0.3), _lease(shared, ttl=0.3)
    waiter._max_wait = 5.0
    slow = Upstream(cache, delay=1.0)  # three lease TTLs
    waiting = Upstream(other)
    results = await asyncio.gather(
        holder.run("k", slow, _value, _always),
        waiter.run("k", waiting, _value, _always),
    )
    assert list(results) == [b"page", b"page"]
    assert (slow.calls, waiting.calls) == (1, 0)
    await holder.drain()


async def test_lease_of_a_vanished_holder_is_taken_over_quickly(shared: FakeServer) -> None:
    lease, cache, _ = _lease(shared, ttl=0.3)
    lease._max_wait = 30.0
    # A holder that died: nobody extends its lease.
    await cache.client.set(LEASE_PREFIX + "k", "someone", px=300)
    fetch = Upstream(cache)
    started = asyncio.get_running_loop().time()
    assert await lease.run("k", fetch, _value, _always) == b"page"
    assert asyncio.get_running_loop().time() - started < 1.0
    assert (fetch.calls, lease.stats["fallback"]) == (1, 0)


async def test_redis_failure_falls_back_to_fetching(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        async def execute_command(self, *_args: Any, **_kwargs: Any) -> None:
            raise ConnectionError("down")

    cache = RedisCache(Broken())  # type: ignore[arg-type]
    lease = RedisLease(cache, ttl=1.0, max_wait=1.0)
    fetch = Upstream(RedisCache(FakeAsyncRedis(decode_responses=True)))
    assert await lease.run("k", fetch, _value, _always) == b"page"
    assert not cache.available  # the breaker opened...
    assert await lease.run("k", fetch, _value, _always) == b"page"
    assert fetch.calls == 2  # ...and the next call does not even try Redis
    assert lease.stats["fallback"] == 2


async def test_shared_upstream_error_keeps_its_type(shared: FakeServer) -> None:
    lease, cache, _ = _lease(shared)
    await cache.client.set(LEASE_PREFIX + "k", "!up t.me returned 502", px=2000)
    with pytest.raises(TelegramUpstreamError, match="502"):
        await lease.run("k", Upstream(cache), _value, _always)
    assert KEY_PREFIX in LEASE_PREFIX
