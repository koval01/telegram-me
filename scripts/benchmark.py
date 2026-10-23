"""In-process benchmark over the recorded fixtures (no network).

Usage:
    uv run python scripts/benchmark.py

Prints median parse time per recorded channel page and median latency of the
main endpoints with the page cache off (every request parses) and on.
"""

import gzip
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.conftest import FIXTURES_DIR, FixtureFetcher, make_settings  # noqa: E402

from telegram_me.app import create_app  # noqa: E402
from telegram_me.telegram.parsing.pages import parse_channel_page  # noqa: E402

CHANNELS = ["durov", "telegram", "stranaua", "tginfo", "nebudetafk", "nebudetgg"]


def median_ms(action: Callable[[], object], runs: int) -> float:
    """Median wall time of `action` in milliseconds, after one warm-up run."""
    action()
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        action()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples) * 1000


def main() -> None:
    print("parse_channel_page (median ms)")
    for channel in CHANNELS:
        html = gzip.decompress((FIXTURES_DIR / f"body_{channel}.txt.gz").read_bytes()).decode()
        print(f"  {channel:12} {median_ms(lambda html=html: parse_channel_page(html), 30):6.1f}")

    feed = {"channels": CHANNELS}
    for cache_mode in ("off", "normal"):
        settings = make_settings(cache_mode=cache_mode, log_level="WARNING")
        with TestClient(create_app(settings, fetcher=FixtureFetcher())) as client:
            print(f"\nCACHE_MODE={cache_mode} (median ms)")
            for channel in ("durov", "tginfo"):
                path = f"/v1/body/{channel}"
                print(f"  GET {path:22} {median_ms(lambda p=path: client.get(p), 40):6.1f}")
            if cache_mode != "off":
                elapsed = median_ms(lambda: client.post("/v1/feed", json=feed), 10)
                print(f"  POST /v1/feed ({len(CHANNELS)} channels) {elapsed:6.1f}")


if __name__ == "__main__":
    main()
