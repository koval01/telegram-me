"""Synthetic traffic over the recorded fixtures, for memory tests and profiling.

`AliasFetcher` answers for any number of made-up channels (`durov_m17`) with
the recorded pages of the real one (`durov`), so a workload can fill the
cache with as many distinct channels as it likes. `run_workload` drives the
ASGI app directly, without an HTTP client, so the memory it measures is the
application's own.
"""

import asyncio
import itertools
import json
import os
import re
import resource
import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Message

from telegram_me.telegram.client import FetchResult, Method
from tests.conftest import MANIFEST, FixtureFetcher

CHANNELS = ("durov", "telegram", "stranaua", "tginfo", "nebudetafk", "nebudetgg")
PREVIEW_CHANNELS = ("durov", "telegram", "stranaua")
_ALIAS = re.compile(r"(?<=/)([a-z]+)_m\d+(?=/|$)")


def alias(channel: str, index: int) -> str:
    """A made-up channel name served with the pages of `channel`."""
    return f"{channel}_m{index}"


class AliasFetcher(FixtureFetcher):
    """`FixtureFetcher` that serves `<channel>_m<N>` with the pages of `<channel>`."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        self.count += 1
        result = await super().fetch(method, _ALIAS.sub(r"\1", path), query=query, headers=headers)
        # Keep only the last request: the workload sends millions of them.
        del self.requests[:-1]
        return result


def requests_for(channel: str, index: int) -> list[tuple[str, str, Any]]:
    """One round of requests touching every endpoint for an alias of `channel`."""
    name = alias(channel, index)
    post_id = MANIFEST["channels"][channel]["post_id"]
    batch: list[tuple[str, str, Any]] = [
        ("GET", f"/v1/body/{name}", None),
        ("GET", f"/v1/body/{name}?position={post_id}", None),
        ("GET", f"/v1/post/{name}/{post_id}", None),
        ("GET", f"/v1/more/{name}/before/{post_id}", None),
        ("GET", f"/v1/more/{name}/after/{post_id}", None),
    ]
    if channel in PREVIEW_CHANNELS:
        batch.append(("GET", f"/v1/preview/{name}", None))
    return batch


def feed_request(index: int) -> tuple[str, str, Any]:
    """`POST /v1/feed` over aliases of every recorded channel."""
    return ("POST", "/v1/feed", {"channels": [alias(c, index) for c in CHANNELS]})


async def call(app: ASGIApp, method: str, path: str, body: Any = None) -> tuple[int, int]:
    """Send one request straight to the ASGI app; returns (status, body size)."""
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
            (b"host", b"memory.test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("memory.test", 80),
    }
    sent = False
    status = 0
    size = 0

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: Message) -> None:
        nonlocal status, size
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            size += len(message.get("body", b""))

    await app(scope, receive, send)
    return status, size


def _requests(channels: int) -> Iterator[tuple[str, str, Any]]:
    index = 0
    while True:
        alias_index = index % channels
        for channel in CHANNELS:
            yield from requests_for(channel, alias_index)
        yield feed_request(alias_index)
        index += 1


async def run_workload(
    app: ASGIApp,
    requests: int,
    *,
    channels: int = 50,
    concurrency: int = 1,
    on_progress: Callable[[int], Awaitable[None]] | None = None,
    progress_every: int = 1000,
) -> dict[int, int]:
    """Send `requests` requests spread over `channels` aliases per recorded channel.

    `concurrency` clients send them at the same time. Returns how many
    responses each status code got.
    """
    statuses: dict[int, int] = {}
    pending = itertools.islice(_requests(channels), requests)
    sent = 0

    async def client() -> None:
        nonlocal sent
        for method, path, body in pending:
            status, _ = await call(app, method, path, body)
            statuses[status] = statuses.get(status, 0) + 1
            sent += 1
            if on_progress is not None and sent % progress_every == 0:
                await on_progress(sent)

    await asyncio.gather(*(client() for _ in range(concurrency)))
    return statuses


def rss_mb() -> float | None:
    """Resident memory of this process in MiB (Linux only, else None)."""
    statm = Path("/proc/self/statm")
    if not statm.exists():
        return None
    pages = int(statm.read_text().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE") / 2**20


def peak_rss_mb() -> float:
    """Peak resident memory of this process in MiB."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 2**10
