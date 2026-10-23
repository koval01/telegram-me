"""Record real t.me responses as offline test fixtures.

Usage:
    uv run python scripts/record_fixtures.py              # re-record everything
    uv run python scripts/record_fixtures.py nebudetgg    # add/refresh some channels

Responses are fetched with the production network stack (`ReqwestFetcher`)
and stored gzipped in tests/fixtures/telegram/ next to manifest.json, which
the tests use to answer upstream requests. After recording, regenerate the
snapshots with `UPDATE_SNAPSHOTS=1 uv run pytest` and review the diff.
"""

import argparse
import asyncio
import gzip
import json
import re
from pathlib import Path
from typing import Any

from telegram_me.telegram.client import Fetcher, FetchResult, Method, ReqwestFetcher

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "telegram"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"

# Channels chosen to cover as many post shapes as possible: nebudetgg has
# nearly every post type, stranaua is a large news channel.
BODY_CHANNELS = ["durov", "telegram", "stranaua", "tginfo", "nebudetafk", "nebudetgg"]
PREVIEW_CHANNELS = ["durov", "telegram", "stranaua"]
# Specific pages with rare content: channel -> post id.
EXTRA_POSTS = {"nebudetgg": 6209}  # inline keyboard button
MISSING_CHANNEL = "zzzzzznotexistingchannelzzzz"
_FIRST_POST = re.compile(r'data-post="[^"/]+/(\d+)"')


class Recorder:
    """Fetches pages and stores them as fixtures."""

    def __init__(self, fetcher: Fetcher, manifest: dict[str, Any]) -> None:
        self.fetcher = fetcher
        self.manifest = manifest

    async def record(
        self, name: str, method: Method, path: str, query: dict[str, int] | None = None
    ) -> FetchResult:
        """Fetch one response, store it and register it in the manifest."""
        headers = {"x-requested-with": "XMLHttpRequest"} if method == "POST" else None
        result = await self.fetcher.fetch(method, path, query=query, headers=headers)
        file_name = f"{name}.txt.gz"
        # mtime=0 keeps archives byte-identical for unchanged content.
        (FIXTURES_DIR / file_name).write_bytes(gzip.compress(result.text.encode(), mtime=0))

        params = {key: str(value) for key, value in (query or {}).items()}
        responses = [
            item
            for item in self.manifest["responses"]
            if (item["method"], item["path"], item["params"]) != (method, path, params)
        ]
        responses.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "status": result.status,
                "content_type": "text/html",
                "file": file_name,
            }
        )
        self.manifest["responses"] = responses
        print(f"{method} {path} {params or ''} -> {result.status} ({len(result.text)} chars)")
        return result

    async def channel(self, channel: str) -> None:
        """Record a channel page, its first post's page and both pagination directions."""
        body = await self.record(f"body_{channel}", "GET", f"/s/{channel}")
        match = _FIRST_POST.search(body.text)
        if match is None:
            raise SystemExit(f"{channel}: no posts found")
        post_id = int(match.group(1))
        self.manifest["channels"][channel] = {"post_id": post_id}
        await self.record(f"post_{channel}_{post_id}", "GET", f"/s/{channel}/{post_id}")
        for direction in ("before", "after"):
            await self.record(
                f"more_{channel}_{direction}_{post_id}",
                "POST",
                f"/s/{channel}",
                {direction: post_id},
            )


async def main(only: list[str]) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    if only and MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
    else:
        manifest = {"channels": {}, "extra_posts": {}, "missing": MISSING_CHANNEL, "responses": []}

    fetcher = ReqwestFetcher(timeout=20)
    recorder = Recorder(fetcher, manifest)
    try:
        for channel in only or BODY_CHANNELS:
            await recorder.channel(channel)
        for channel, post_id in EXTRA_POSTS.items():
            if not only or channel in only:
                await recorder.record(f"post_{channel}_{post_id}", "GET", f"/s/{channel}/{post_id}")
                manifest["extra_posts"][channel] = post_id
        if not only:
            for channel in PREVIEW_CHANNELS:
                await recorder.record(f"preview_{channel}", "GET", f"/{channel}")
            await recorder.record("body_missing", "GET", f"/s/{MISSING_CHANNEL}")
            await recorder.record("preview_missing", "GET", f"/{MISSING_CHANNEL}")
    finally:
        await fetcher.close()

    manifest["responses"].sort(
        key=lambda item: (item["path"], item["method"], sorted(item["params"].items()))
    )
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("channels", nargs="*", help="record only these channels")
    asyncio.run(main(parser.parse_args().channels))
