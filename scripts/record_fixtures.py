"""Record real t.me responses as offline test fixtures.

Usage:
    uv run python scripts/record_fixtures.py

Writes gzipped raw upstream responses to tests/fixtures/telegram/ together with
manifest.json, which the snapshot tests use to mock t.me via respx.
Re-run it only when you intentionally want to refresh the fixtures, then
regenerate snapshots with UPDATE_SNAPSHOTS=1 pytest and review the diff.
"""

import asyncio
import gzip
import json
from pathlib import Path

import httpx

from telegram_me.telegram.request import TELEGRAM_HEADERS

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "telegram"

# Channels chosen to cover as many post shapes as possible
# (media, replies, forwards, reactions, polls, link previews, inline buttons).
BODY_CHANNELS = ["durov", "telegram", "stranaua", "tginfo", "nebudetafk"]
PREVIEW_CHANNELS = ["durov", "telegram", "stranaua"]
MISSING_CHANNEL = "zzzzzznotexistingchannelzzzz"


async def fetch(
    client: httpx.AsyncClient, method: str, path: str, params: dict | None = None
) -> httpx.Response:
    headers = dict(TELEGRAM_HEADERS)
    if method == "POST":
        headers["x-requested-with"] = "XMLHttpRequest"
    return await client.request(
        method, f"https://t.me/{path}", params=params, headers=headers, follow_redirects=False
    )


def save(manifest: list, name: str, method: str, path: str, params: dict | None,
         response: httpx.Response) -> None:
    file_name = f"{name}.txt.gz"
    # mtime=0 keeps the archive byte-identical for unchanged content.
    (FIXTURES_DIR / file_name).write_bytes(
        gzip.compress(response.text.encode("utf-8"), mtime=0)
    )
    manifest.append(
        {
            "method": method,
            "path": f"/{path}",
            "params": {k: str(v) for k, v in (params or {}).items()},
            "status": response.status_code,
            "content_type": response.headers.get("content-type", "text/html"),
            "file": file_name,
        }
    )
    print(f"{method} /{path} {params or ''} -> {response.status_code} ({len(response.text)} chars)")


def first_post_id(html: str) -> int:
    marker = 'data-post="'
    start = html.index(marker) + len(marker)
    return int(html[start: html.index('"', start)].split("/")[1])


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    channels_meta: dict[str, dict] = {}

    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        for channel in BODY_CHANNELS:
            body = await fetch(client, "GET", f"s/{channel}/None")
            save(manifest, f"body_{channel}", "GET", f"s/{channel}/None", None, body)

            post_id = first_post_id(body.text)
            channels_meta[channel] = {"post_id": post_id}

            post = await fetch(client, "GET", f"s/{channel}/{post_id}")
            save(manifest, f"post_{channel}_{post_id}", "GET", f"s/{channel}/{post_id}", None, post)

            for direction in ("before", "after"):
                params = {direction: post_id}
                more = await fetch(client, "POST", f"s/{channel}", params)
                save(manifest, f"more_{channel}_{direction}_{post_id}", "POST",
                     f"s/{channel}", params, more)

        for channel in PREVIEW_CHANNELS:
            preview = await fetch(client, "GET", channel)
            save(manifest, f"preview_{channel}", "GET", channel, None, preview)

        missing_body = await fetch(client, "GET", f"s/{MISSING_CHANNEL}/None")
        save(manifest, "body_missing", "GET", f"s/{MISSING_CHANNEL}/None", None, missing_body)
        missing_preview = await fetch(client, "GET", MISSING_CHANNEL)
        save(manifest, "preview_missing", "GET", MISSING_CHANNEL, None, missing_preview)

    (FIXTURES_DIR / "manifest.json").write_text(
        json.dumps({"channels": channels_meta, "missing": MISSING_CHANNEL,
                    "responses": manifest}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
