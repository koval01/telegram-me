"""Memory profile of the service over the recorded fixtures (no network).

Usage:
    uv run python scripts/memory_profile.py [--requests 20000] [--channels 50]

Prints:

- imports: memory of a fresh interpreter after importing what the granian
  main process and a worker need (each measured in its own subprocess);
- cache entries: what one cached page costs in `MemoryCache`, by format;
- workload: resident memory and Python allocations (tracemalloc) of one
  in-process app while it serves synthetic traffic over many channels,
  sampled every few thousand requests to show growth (a leak shows up as
  a steady climb after the cache is full).

RSS is read from /proc, so run it on Linux (for example in the Docker image
build stage or `docker run -v .:/src python:3.14-slim`) for meaningful numbers;
on macOS only the peak RSS is available.
"""

import argparse
import asyncio
import gc
import gzip
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.conftest import FIXTURES_DIR, make_settings  # noqa: E402
from tests.workload import AliasFetcher, peak_rss_mb, rss_mb, run_workload  # noqa: E402

from telegram_me.app import create_app  # noqa: E402
from telegram_me.cache import MemoryCache  # noqa: E402
from telegram_me.schemas.channel import ChannelBody  # noqa: E402
from telegram_me.services.channels import dump_json, encode, validate_with_posts  # noqa: E402
from telegram_me.telegram.parsing.pages import parse_channel_page  # noqa: E402

MiB = 2**20

# What each process imports. The main process only supervises workers.
IMPORT_STAGES = {
    "interpreter": "",
    "main process (telegram-me serve)": "import telegram_me.server",
    "worker (app built)": (
        "import telegram_me.server as s; from telegram_me.settings import Settings; s.asgi()"
    ),
}

_MEASURE = """
import sys, resource
{code}
statm = None
try:
    statm = open("/proc/self/status").read()
except OSError:
    pass
if statm:
    fields = dict(line.split(":", 1) for line in statm.splitlines())
    print(int(fields["VmRSS"].split()[0]) / 1024, int(fields["RssAnon"].split()[0]) / 1024,
          len(sys.modules))
else:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(peak / 2**20 if sys.platform == "darwin" else peak / 1024, 0, len(sys.modules))
"""


def imports() -> None:
    print("Imports (fresh interpreter)")
    print(f"  {'stage':34} {'RSS MiB':>8} {'anon MiB':>9} {'modules':>8}")
    for name, code in IMPORT_STAGES.items():
        output = subprocess.run(  # noqa: S603 - our own interpreter and code
            [sys.executable, "-c", _MEASURE.format(code=code)],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
            env={"LOG_LEVEL": "WARNING", "PATH": ""},
        ).stdout.split()
        rss, anon, modules = float(output[0]), float(output[1]), int(output[2])
        print(f"  {name:34} {rss:8.1f} {anon:9.1f} {modules:8}")


async def cache_entries() -> None:
    print("\nOne cached channel page (KiB): as str (2.0.0), UTF-8, in MemoryCache now")
    columns = ("html", "raw str", "raw utf8", "raw cache", "body str", "body utf8", "body cache")
    print(f"  {'channel':11}" + "".join(f"{c:>11}" for c in columns))
    for path in sorted(FIXTURES_DIR.glob("body_*.txt.gz")):
        html = gzip.decompress(path.read_bytes()).decode()
        if "tgme_channel_info" not in html:
            continue
        data = parse_channel_page(html)
        raw = encode(data)
        rendered = dump_json(validate_with_posts(ChannelBody, data, ("content", "posts")))
        sizes = [len(html)]
        for value in (raw, rendered):
            probe = MemoryCache(max_bytes=2**30)
            await probe.set("page:full:channel:0", value, ttl=60)
            sizes += [sys.getsizeof(value.decode()), len(value), probe.size]
        name = path.name.removeprefix("body_").removesuffix(".txt.gz")
        print(f"  {name:11}" + "".join(f"{size / 1024:11.1f}" for size in sizes))


async def workload(
    requests: int, channels: int, concurrency: int, cache_mb: int, redis_url: str | None
) -> None:
    print(
        f"\nWorkload: {requests} requests over {channels} aliases of each recorded channel, "
        f"{concurrency} at a time, "
        + (f"REDIS_URL={redis_url}" if redis_url else f"MEMORY_CACHE_MAX_MB={cache_mb}")
    )
    settings = make_settings(log_level="ERROR", memory_cache_max_mb=cache_mb, redis_url=redis_url)
    fetcher = AliasFetcher()
    app = create_app(settings, fetcher=fetcher)
    tracemalloc.start()
    started = time.perf_counter()
    print(f"  {'requests':>9} {'RSS MiB':>8} {'traced MiB':>11} {'gc objects':>11} {'upstream':>9}")

    async def sample(sent: int) -> None:
        gc.collect()
        traced, _ = tracemalloc.get_traced_memory()
        rss = rss_mb()
        rss_text = f"{rss:8.1f}" if rss is not None else f"{'n/a':>8}"
        print(
            f"  {sent:9} {rss_text} {traced / MiB:11.1f} {len(gc.get_objects()):11} "
            f"{fetcher.count:9}"
        )

    async with app.router.lifespan_context(app):
        await sample(0)
        statuses = await run_workload(
            app,
            requests,
            channels=channels,
            concurrency=concurrency,
            on_progress=sample,
            progress_every=max(requests // 10, 1),
        )
        cache = app.state.container.cache
        _, peak = tracemalloc.get_traced_memory()
        elapsed = time.perf_counter() - started
        print(f"  statuses {statuses}, {requests / elapsed:.0f} req/s under tracemalloc")
        print(f"  traced peak {peak / MiB:.1f} MiB, peak RSS {peak_rss_mb():.1f} MiB")
        if isinstance(cache, MemoryCache):
            print(f"  MemoryCache: {len(cache)} entries, {cache.size / MiB:.1f} MiB accounted")
        snapshot = tracemalloc.take_snapshot()
    tracemalloc.stop()
    print("  top allocations still alive at the end:")
    for stat in snapshot.statistics("lineno")[:8]:
        where = str(stat.traceback[0]).removeprefix(f"{ROOT}/")
        print(f"    {stat.size / MiB:6.2f} MiB {stat.count:7} blocks  {where}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requests", type=int, default=20_000)
    parser.add_argument("--channels", type=int, default=50, help="aliases per recorded channel")
    parser.add_argument("--concurrency", type=int, default=1, help="simultaneous clients")
    parser.add_argument("--cache-mb", type=int, default=64, help="MEMORY_CACHE_MAX_MB")
    parser.add_argument("--redis-url", help="use Redis instead of the memory cache")
    parser.add_argument("--skip-imports", action="store_true")
    args = parser.parse_args()
    if not args.skip_imports:
        imports()
    asyncio.run(cache_entries())
    asyncio.run(
        workload(args.requests, args.channels, args.concurrency, args.cache_mb, args.redis_url)
    )


if __name__ == "__main__":
    main()
