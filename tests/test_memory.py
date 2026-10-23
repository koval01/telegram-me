"""Memory regression guards: sustained traffic must not grow a worker.

Requests go straight to the ASGI app over the recorded fixtures (see
`tests/workload.py`). Python allocations are checked on every platform;
resident memory, which also covers native allocations (the Rust parser and ranker, the
compression and serialization libraries), where /proc is available (Linux,
so in CI).
Deselect with `-m "not memory"` for a quicker local run.
"""

import gc
import sys
import tracemalloc
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

import pytest
from fastapi import FastAPI

from telegram_me.app import create_app
from telegram_me.cache import MemoryCache
from tests.conftest import make_settings
from tests.workload import AliasFetcher, rss_mb, run_workload

pytestmark = pytest.mark.memory

MiB = 2**20
# Allowed growth after the warm-up. A leak of one parsed page per request
# would be ~100 000 blocks and tens of MiB over these workloads.
MAX_BLOCKS = 5_000
MAX_RSS_MB = 12


class Growth(NamedTuple):
    """Growth of Python-allocated blocks and of resident memory (MiB, Linux only)."""

    blocks: int
    rss: float | None

    def check(self) -> None:
        assert self.blocks < MAX_BLOCKS, f"{self.blocks} more Python blocks allocated"
        if self.rss is not None:
            assert self.rss < MAX_RSS_MB, f"RSS grew by {self.rss:.1f} MiB"


@asynccontextmanager
async def running_app(**overrides: Any) -> AsyncIterator[FastAPI]:
    app = create_app(make_settings(log_level="ERROR", **overrides), fetcher=AliasFetcher())
    async with app.router.lifespan_context(app):
        yield app


async def _growth(app: FastAPI, warmup: int, requests: int, channels: int) -> Growth:
    """Memory growth over `requests` requests that follow a warm-up."""
    await run_workload(app, warmup, channels=channels)
    gc.collect()
    blocks_before, rss_before = sys.getallocatedblocks(), rss_mb()
    await run_workload(app, requests, channels=channels)
    gc.collect()
    blocks_after, rss_after = sys.getallocatedblocks(), rss_mb()
    rss = None if rss_before is None or rss_after is None else rss_after - rss_before
    return Growth(blocks_after - blocks_before, rss)


async def test_parsing_every_request_does_not_leak() -> None:
    # Without a page cache every request fetches and parses a page, so a leak
    # in the native parser, validation or rendering adds up quickly.
    async with running_app(cache_mode="off") as app:
        growth = await _growth(app, warmup=60, requests=300, channels=3)
    growth.check()


async def test_cached_traffic_does_not_leak() -> None:
    # Every alias is cached after the warm-up; the rest are cache hits (and
    # re-fetches of entries that expired meanwhile).
    async with running_app(cache_mode="normal") as app:
        growth = await _growth(app, warmup=300, requests=2500, channels=8)
    growth.check()


async def test_memory_cache_stays_within_its_limit() -> None:
    limit = 1 * MiB
    tracemalloc.start()
    try:
        async with running_app(memory_cache_max_mb=1) as app:
            cache = app.state.container.cache
            assert isinstance(cache, MemoryCache)
            await run_workload(app, 200, channels=6)
            gc.collect()
            snapshot = tracemalloc.take_snapshot()
            entries, accounted = len(cache), cache.size
    finally:
        tracemalloc.stop()
    assert entries > 20
    assert limit * 0.9 < accounted <= limit
    # Entries live in the native cache, outside the Python heap: that what it
    # accounts covers what they really hold is checked with a counting
    # allocator by rust/telegram-me-native/tests/cache_memory.rs. Here, no
    # cached value may be kept on the Python side.
    held = snapshot.filter_traces(
        [tracemalloc.Filter(inclusive=True, filename_pattern="*/telegram_me/cache.py")]
    ).statistics("filename")
    held_bytes = sum(stat.size for stat in held)
    assert held_bytes < 64 * 1024, f"cache.py holds {held_bytes / MiB:.2f} MiB"
