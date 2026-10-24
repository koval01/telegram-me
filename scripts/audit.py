"""Where a worker spends its time: per endpoint and per pipeline stage (no network).

Usage:
    uv run python scripts/audit.py [--runs 200] [--stages] [--json out.json]

Drives the ASGI app directly (no HTTP client) over the recorded fixtures, so
only the service is measured. For every endpoint it prints the median wall
time, the median CPU time of the whole process (all threads) and the bytes
Python allocates per request (tracemalloc, measured separately). `--stages`
adds a breakdown of the cold page pipeline and of a feed build; `--memory N`
only measures the resident memory of N 100-channel feeds (run it on Linux).

Feed and previews requests use made-up aliases of the recorded channels
(`durov_m3`) that also get their own username, so a 100-channel feed ranks
2 000 distinct posts without extra fixtures.
"""

import argparse
import asyncio
import gc
import gzip
import json
import re
import statistics
import sys
import time
import tracemalloc
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.conftest import FIXTURES_DIR, MANIFEST, make_settings  # noqa: E402
from tests.workload import CHANNELS, AliasFetcher, alias, call  # noqa: E402

from telegram_me.app import create_app  # noqa: E402
from telegram_me.telegram.client import FetchResult, Method  # noqa: E402

type Request = tuple[str, str, Any]

_ALIAS = re.compile(r"(?<=/)([a-z]+)_m\d+(?=/|$)")


class DistinctFetcher(AliasFetcher):
    """`AliasFetcher` whose aliases also show their own username on the page.

    Otherwise every alias of @durov would be @durov, and the feed would drop
    their posts as repeats of one channel's.
    """

    async def fetch(self, method: Method, path: str, **kwargs: Any) -> FetchResult:  # noqa: D102
        result = await super().fetch(method, path, **kwargs)
        match = _ALIAS.search(path)
        if match is None:
            return result
        text = re.sub(f">@{match[1]}<", f">@{match[0]}<", result.text, flags=re.IGNORECASE)
        return FetchResult(result.status, text)


PREVIEWS = ("durov", "telegram", "stranaua")


def _feed(count: int) -> Request:
    names = [alias(CHANNELS[i % len(CHANNELS)], i // len(CHANNELS)) for i in range(count)]
    return ("POST", "/v1/feed", {"channels": names})


def _previews(count: int) -> Request:
    names = [alias(PREVIEWS[i % len(PREVIEWS)], i // len(PREVIEWS)) for i in range(count)]
    return ("POST", "/v1/previews", {"channels": names})


def _graphql(query: str) -> Request:
    return ("POST", "/graphql", {"query": query})


POST_ID = MANIFEST["channels"]["durov"]["post_id"]
GQL_BODY = (
    '{ body(channel: "durov") { channel { username title { string } } '
    "posts { id content { text { string } } footer { views date { unix } } } } }"
)

# (label, request, warm): warm requests are sent once before measuring, so
# they hit the cache; cold ones are measured with CACHE_MODE=off.
ENDPOINTS: list[tuple[str, Request]] = [
    ("GET /healthz", ("GET", "/healthz", None)),
    ("GET /v1/body/durov", ("GET", "/v1/body/durov", None)),
    ("GET /v1/body/tginfo", ("GET", "/v1/body/tginfo", None)),
    ("GET /v1/post/durov/{id}", ("GET", f"/v1/post/durov/{POST_ID}", None)),
    ("GET /v1/more/durov/before/{id}", ("GET", f"/v1/more/durov/before/{POST_ID}", None)),
    ("GET /v1/preview/durov", ("GET", "/v1/preview/durov", None)),
    ("POST /v1/previews (3)", _previews(3)),
    ("POST /v1/previews (100)", _previews(100)),
    ("POST /v1/feed (6)", _feed(6)),
    ("POST /v1/feed (30)", _feed(30)),
    ("POST /v1/feed (100)", _feed(100)),
    ("POST /graphql body", _graphql(GQL_BODY)),
]
COLD = {
    "GET /v1/body/durov",
    "GET /v1/body/tginfo",
    "GET /v1/post/durov/{id}",
    "GET /v1/more/durov/before/{id}",
}


async def _measure(
    send: Callable[[], Awaitable[tuple[int, int]]], runs: int
) -> tuple[float, float, float, int]:
    """Median wall ms, median CPU ms, allocated KiB per request, response bytes."""
    status, size = await send()
    if status != 200:
        msg = f"unexpected status {status}"
        raise RuntimeError(msg)
    walls, cpus = [], []
    gc.collect()
    for _ in range(runs):
        wall, cpu = time.perf_counter(), time.process_time()
        await send()
        walls.append(time.perf_counter() - wall)
        cpus.append(time.process_time() - cpu)
    # Allocations: what Python allocates in total while serving one request.
    tracemalloc.start()
    tracemalloc.reset_peak()
    before, _ = tracemalloc.get_traced_memory()
    await send()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return (
        statistics.median(walls) * 1000,
        statistics.median(cpus) * 1000,
        (peak - before) / 1024,
        size,
    )


async def endpoints(runs: int, results: dict[str, Any]) -> None:
    for cache_mode in ("normal", "off"):
        settings = make_settings(cache_mode=cache_mode, log_level="ERROR")
        app = create_app(settings, fetcher=DistinctFetcher())
        title = "cache hit" if cache_mode == "normal" else "cache off (fetch + parse + render)"
        print(f"\n{title}")
        print(f"  {'request':32} {'wall ms':>8} {'CPU ms':>8} {'peak KiB':>9} {'bytes':>9}")
        async with app.router.lifespan_context(app):
            for label, (method, path, body) in ENDPOINTS:
                if cache_mode == "off" and label not in COLD:
                    continue

                async def send(
                    a: Any = app, m: str = method, p: str = path, b: Any = body
                ) -> tuple[int, int]:
                    return await call(a, m, p, b)

                n = runs if "100" not in label else max(runs // 10, 10)
                wall, cpu, peak, size = await _measure(send, n)
                results[f"{cache_mode}:{label}"] = {
                    "wall_ms": wall,
                    "cpu_ms": cpu,
                    "peak_kib": peak,
                    "bytes": size,
                }
                print(f"  {label:32} {wall:8.2f} {cpu:8.2f} {peak:9.0f} {size:9}")


async def mixed(runs: int, results: dict[str, Any]) -> None:
    """Latency of cached `/v1/body` while other clients build 100-channel feeds.

    Shows how long a feed request holds the event loop (and the GIL) away
    from every other request of the worker.
    """
    settings = make_settings(log_level="ERROR")
    app = create_app(settings, fetcher=DistinctFetcher())
    method, path, body = _feed(100)
    async with app.router.lifespan_context(app):
        await call(app, method, path, body)
        await call(app, "GET", "/v1/body/durov")
        stop = asyncio.Event()
        feeds = 0

        async def feed_client() -> None:
            nonlocal feeds
            while not stop.is_set():
                await call(app, method, path, body)
                feeds += 1

        latencies: list[float] = []
        interval = 0.02

        async def body_client(offset: float) -> None:
            # Open loop: each request is due at a fixed time and its latency
            # counts from then, so time spent waiting for a blocked event
            # loop is included.
            first = time.perf_counter() + offset
            for index in range(runs):
                due = first + index * interval
                await asyncio.sleep(max(due - time.perf_counter(), 0))
                await call(app, "GET", "/v1/body/durov")
                latencies.append(time.perf_counter() - due)

        cpu, wall = time.process_time(), time.perf_counter()
        background = [asyncio.create_task(feed_client()) for _ in range(1)]
        await asyncio.gather(*(body_client(i * interval / 2) for i in range(2)))
        stop.set()
        await asyncio.gather(*background)
        elapsed, cpu = time.perf_counter() - wall, time.process_time() - cpu
    latencies.sort()
    row = {
        "body_p50_ms": latencies[len(latencies) // 2] * 1000,
        "body_p99_ms": latencies[int(len(latencies) * 0.99)] * 1000,
        "body_max_ms": latencies[-1] * 1000,
        "feeds_per_s": feeds / elapsed,
        "cpu_per_feed_ms": cpu / max(feeds, 1) * 1000,
    }
    results["mixed"] = row
    print("\ncached /v1/body/durov, 2 clients at 50 req/s, next to a 100-channel feed loop")
    for name, value in row.items():
        print(f"  {name:16} {value:8.2f}")


async def feed_memory(requests: int, results: dict[str, Any]) -> None:
    """Resident memory of a worker serving 100-channel feeds (Linux: current RSS)."""
    from tests.workload import peak_rss_mb, rss_mb  # noqa: PLC0415

    settings = make_settings(log_level="ERROR")
    app = create_app(settings, fetcher=DistinctFetcher())
    method, path, body = _feed(100)
    async with app.router.lifespan_context(app):
        await call(app, method, path, body)
        gc.collect()
        warm = rss_mb()
        for _ in range(requests):
            await call(app, method, path, body)
        gc.collect()
        row = {"rss_warm_mb": warm, "rss_after_mb": rss_mb(), "peak_rss_mb": peak_rss_mb()}
    results["feed_memory"] = row
    print(f"\n{requests} x 100-channel feed, memory (MiB)")
    for name, value in row.items():
        print(f"  {name:16} {value if value is None else round(value, 1)}")


def _median_ms(action: Callable[[], object], runs: int) -> float:
    action()
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        action()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples) * 1000


def stages(runs: int, results: dict[str, Any]) -> None:
    """Cold page pipeline and the native feed build, stage by stage (single call)."""
    import telegram_me_native as native  # noqa: PLC0415
    from pydantic_core import from_json  # noqa: PLC0415

    from telegram_me.cache import _compress  # noqa: PLC0415
    from telegram_me.schemas.channel import ChannelBody  # noqa: PLC0415
    from telegram_me.services.channels import (  # noqa: PLC0415
        decode_page,
        dump_json,
        encode,
        validate_with_posts,
    )

    print("\ncold page pipeline per channel page (median ms)")
    columns = ("parse", "from_json", "validate", "dump", "encode", "compress", "decode")
    print(f"  {'page':11}" + "".join(f"{c:>10}" for c in columns))
    pages: dict[str, Any] = {}
    for channel in CHANNELS:
        html = gzip.decompress((FIXTURES_DIR / f"body_{channel}.txt.gz").read_bytes()).decode()
        result = native.parse_channel_page(html, False)
        assert result is not None
        page_json = result[0]
        data = from_json(page_json)
        model = validate_with_posts(ChannelBody, data, ("content", "posts"))
        rendered = dump_json(model)
        raw = encode(data)
        pages[channel] = data
        row = {
            "parse": _median_ms(lambda h=html: native.parse_channel_page(h, False), runs),
            "from_json": _median_ms(lambda j=page_json: from_json(j), runs),
            "validate": _median_ms(
                lambda d=data: validate_with_posts(ChannelBody, d, ("content", "posts")), runs
            ),
            "dump": _median_ms(lambda m=model: dump_json(m), runs),
            "encode": _median_ms(lambda d=data: encode(d), runs),
            "compress": _median_ms(lambda r=rendered: _compress(r), runs),
            "decode": _median_ms(lambda r=raw: decode_page(r), runs),
        }
        results[f"stage:{channel}"] = row
        print(f"  {channel:11}" + "".join(f"{row[c]:10.2f}" for c in columns))

    if not hasattr(native, "build_feed"):  # before 2.1
        return
    # 100 cached pages with distinct usernames: 2 000 posts.
    feed_pages = []
    for index in range(100):
        page = pages[CHANNELS[index % len(CHANNELS)]]
        renamed = {**page, "channel": {**page["channel"], "username": f"channel{index}"}}
        feed_pages.append((f"channel{index}", encode(renamed)))
    options = native.RankOptions(now=int(time.time()))
    feed, _ = native.build_feed(feed_pages, options)
    elapsed = _median_ms(lambda: native.build_feed(feed_pages, options), max(runs // 10, 10))
    results["stage:feed100"] = {"build_feed": elapsed, "bytes": len(feed)}
    print(f"\nnative build_feed over 100 cached pages, 2000 posts: {elapsed:.2f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--stages", action="store_true", help="add the per-stage breakdown")
    parser.add_argument("--json", type=Path, help="also write the numbers to this file")
    parser.add_argument(
        "--memory", type=int, metavar="N", help="only measure memory over N feed requests"
    )
    args = parser.parse_args()
    results: dict[str, Any] = {}
    if args.memory:
        asyncio.run(feed_memory(args.memory, results))
        if args.json:
            args.json.write_text(json.dumps(results, indent=2) + "\n")
        return
    asyncio.run(endpoints(args.runs, results))
    asyncio.run(mixed(args.runs, results))
    if args.stages:
        stages(args.runs, results)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
