"""Duplicate-heavy load across worker processes sharing one Redis (no network).

Usage:
    docker run -d --name tme-valkey -p 6390:6379 valkey/valkey:8
    uv run python scripts/coalescing_load.py --redis redis://127.0.0.1:6390/0
    uv run python scripts/coalescing_load.py --redis '' --peers   # peer layer, no Redis

With `--peers` the processes are nodes of the peer layer on localhost ports
(PEER_PORT 17946 and up), with or without Redis.

Starts `--processes` worker processes (think 2 servers x 3 granian workers),
each with its own app, in-process cache and single flights, all on the same
Redis. Every process drives its app straight through ASGI with `--clients`
concurrent clients that pick requests at random from a small hot set (every
recorded channel through every endpoint, a feed and GraphQL), so all
processes want the same keys at the same moments and the page cache
(CACHE_MODE=normal, 10 s) expires several times during the run.

t.me is replaced by the recorded fixtures, answered after `--delay` seconds.
Prints, summed over all processes: requests served, upstream fetches (in
total and per distinct upstream request), CPU time of the workers, and
latency percentiles. Run it on two checkouts to compare them; settings a
checkout does not know are ignored.
"""

import argparse
import asyncio
import json
import multiprocessing as mp
import random
import resource
import secrets
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.conftest import MANIFEST, FixtureFetcher, make_settings  # noqa: E402
from tests.workload import CHANNELS, PREVIEW_CHANNELS, call  # noqa: E402

from telegram_me.app import create_app  # noqa: E402
from telegram_me.telegram.client import FetchResult, Method  # noqa: E402


def hot_set() -> list[tuple[str, str, Any]]:
    """Requests every client picks from."""
    requests: list[tuple[str, str, Any]] = []
    for channel in CHANNELS:
        post_id = MANIFEST["channels"][channel]["post_id"]
        requests += [
            ("GET", f"/v1/body/{channel}", None),
            ("GET", f"/v1/body/{channel}?position={post_id}", None),
            ("GET", f"/v1/post/{channel}/{post_id}", None),
            ("GET", f"/v1/more/{channel}/before/{post_id}", None),
            ("GET", f"/v1/more/{channel}/after/{post_id}", None),
        ]
    requests += [("GET", f"/v1/preview/{channel}", None) for channel in PREVIEW_CHANNELS]
    requests.append(("POST", "/v1/feed", {"channels": list(CHANNELS)}))
    query = '{ a: body(channel: "durov") { channel { username } } b: feed(channels: %s) { id } }'
    requests.append(("POST", "/graphql", {"query": query % json.dumps(list(CHANNELS))}))
    return requests


class SlowFetcher(FixtureFetcher):
    """Recorded responses after a delay, counted across processes."""

    def __init__(self, delay: float, counter: Any) -> None:
        super().__init__()
        self.delay = delay
        self.counter = counter
        self.fetched: list[str] = []

    async def fetch(  # noqa: D102
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        with self.counter.get_lock():
            self.counter.value += 1
        self.fetched.append(f"{method} {path} {dict(query or {})}")
        await asyncio.sleep(self.delay)
        return await super().fetch(method, path, query=query, headers=headers)


def cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


async def drive(args: argparse.Namespace, counter: Any, barrier: Any) -> dict[str, Any]:
    peers: dict[str, Any] = {}
    if args.peers:
        peers = {
            "enable_peers": True,
            "peer_secret": args.peer_secret,
            "peer_port": 17946,
            "web_concurrency": args.processes,
            "peers": ["127.0.0.1"],
            "peer_advertise_addr": "127.0.0.1",
        }
    settings = make_settings(
        redis_url=args.redis or None,
        cache_mode="normal",
        log_level=args.log_level,
        telegram_max_concurrency=32,
        shared_coalescing=not args.no_lease,
        **peers,
    )
    fetcher = SlowFetcher(args.delay, counter)
    app = create_app(settings, fetcher=fetcher)
    requests = hot_set()
    latencies: list[float] = []
    statuses: Counter[int] = Counter()
    async with app.router.lifespan_context(app):
        if args.peers:
            node = app.state.container.peers
            while len(node._members) < args.processes - 1:  # noqa: ASYNC110 - native state
                await asyncio.sleep(0.05)
        await asyncio.to_thread(barrier.wait)
        started_cpu, started = cpu_seconds(), time.perf_counter()
        deadline = started + args.duration

        async def client() -> None:
            rng = random.Random()  # noqa: S311
            while time.perf_counter() < deadline:
                method, path, body = rng.choice(requests)
                t0 = time.perf_counter()
                status, _ = await call(app, method, path, body)
                latencies.append(time.perf_counter() - t0)
                statuses[status] += 1

        lag = 0.0

        async def probe() -> None:
            # Event-loop stalls: how late a short sleep wakes up.
            nonlocal lag
            while time.perf_counter() < deadline:
                t0 = time.perf_counter()
                await asyncio.sleep(0.01)
                lag = max(lag, time.perf_counter() - t0 - 0.01)

        await asyncio.gather(probe(), *(client() for _ in range(args.clients)))
        elapsed, cpu = time.perf_counter() - started, cpu_seconds() - started_cpu
        lease = getattr(app.state.container, "lease", None)
        lease_stats = dict(getattr(lease, "stats", {}))
        if args.peers:
            lease_stats.update(app.state.container.peers.node.stats())
    paths = Counter(fetcher.fetched)
    return {
        "requests": len(latencies),
        "elapsed": elapsed,
        "cpu": cpu,
        "latencies": latencies,
        "statuses": dict(statuses),
        "paths": dict(paths),
        "lease": lease_stats,
        "lag": lag,
    }


def worker(args: argparse.Namespace, counter: Any, barrier: Any, results: Any) -> None:
    results.put(asyncio.run(drive(args, counter, barrier)))


def percentile(values: list[float], q: float) -> float:
    return statistics.quantiles(values, n=1000)[int(q * 1000) - 1] if len(values) > 1 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--redis", default="redis://127.0.0.1:6390/0")
    parser.add_argument("--processes", type=int, default=6)
    parser.add_argument("--clients", type=int, default=32, help="per process")
    parser.add_argument("--duration", type=float, default=25.0)
    parser.add_argument("--delay", type=float, default=0.15, help="t.me latency, seconds")
    parser.add_argument("--verbose", action="store_true", help="list upstream requests")
    parser.add_argument("--log-level", default="ERROR")
    parser.add_argument("--no-lease", action="store_true", help="SHARED_COALESCING=false")
    parser.add_argument("--peers", action="store_true", help="ENABLE_PEERS on localhost")
    args = parser.parse_args()
    args.peer_secret = secrets.token_hex(32)

    import redis  # noqa: PLC0415

    if args.redis:
        redis.Redis.from_url(args.redis).flushdb()
    context = mp.get_context("spawn")
    counter = context.Value("q", 0)
    barrier = context.Barrier(args.processes)
    results = context.Queue()
    processes = [
        context.Process(target=worker, args=(args, counter, barrier, results))
        for _ in range(args.processes)
    ]
    for process in processes:
        process.start()
    reports = [results.get() for _ in processes]
    for process in processes:
        process.join()

    served = sum(r["requests"] for r in reports)
    elapsed = max(r["elapsed"] for r in reports)
    cpu = sum(r["cpu"] for r in reports)
    latencies = [x for r in reports for x in r["latencies"]]
    statuses: Counter[int] = Counter()
    paths: Counter[str] = Counter()
    for report in reports:
        statuses.update({int(k): v for k, v in report["statuses"].items()})
        paths.update(report["paths"])
    fetches = counter.value
    lease: Counter[str] = Counter()
    for report in reports:
        lease.update(report["lease"])
    if args.verbose:
        for path, count in sorted(paths.items()):
            print(f"  {count:4} {path}")
    print(f"processes {args.processes} x clients {args.clients}, {elapsed:.1f} s")
    print(f"requests       {served} ({served / elapsed:.0f}/s), statuses {dict(statuses)}")
    print(
        f"upstream       {fetches} fetches, {len(paths)} distinct, {fetches / len(paths):.1f} each"
    )
    print(f"cpu            {cpu:.1f} s, {cpu / served * 1000:.3f} ms per request")
    if lease:
        print(f"lease          {dict(lease)}")
    print(f"loop stall     max {max(r['lag'] for r in reports) * 1000:.0f} ms")
    print(
        f"latency        p50 {percentile(latencies, 0.5) * 1000:.2f} ms, "
        f"p99 {percentile(latencies, 0.99) * 1000:.1f} ms"
    )


if __name__ == "__main__":
    main()
