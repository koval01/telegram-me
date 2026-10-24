"""A heartbeat to Redis: every process writes its marker with a short TTL.

It keeps a pooled connection open, so requests do not pay for reconnecting
to a distant TLS endpoint. It notices an outage or a recovery within one
interval, and the live markers (`tme:v2:node:*`) show which processes of
which servers are running.
"""

import asyncio
import json
import logging
import os
import platform
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from telegram_me import __version__
from telegram_me.cache import KEY_PREFIX, RedisCache

logger = logging.getLogger(__name__)

NODE_PREFIX = "node:"
# A marker outlives a few missed beats, so one slow write does not make the
# node look gone.
_TTL_INTERVALS = 3


def node_id() -> str:
    """This process: `<host>:<pid>`."""
    return f"{platform.node()}:{os.getpid()}"


@asynccontextmanager
async def beating(
    shared: RedisCache, interval: float, *, peer: str | None = None
) -> AsyncIterator[None]:
    """Write this process's marker every `interval` seconds while the block runs.

    An `interval` of 0 disables the heartbeat. `peer` is this process's
    address in the peer layer, for other nodes to find it (`peers`).
    """
    if interval <= 0:
        yield
        return
    key = NODE_PREFIX + node_id()
    started = time.time()

    async def loop() -> None:
        while True:
            fields: dict[str, object] = {
                "version": __version__,
                "started": round(started),
                "at": round(time.time(), 3),
            }
            if peer is not None:
                fields["peer"] = peer
            marker = json.dumps(fields, separators=(",", ":"))
            await shared.heartbeat(key, marker, interval * _TTL_INTERVALS)
            await asyncio.sleep(interval)

    task = asyncio.create_task(loop(), name="redis-heartbeat")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # Leave without waiting for the TTL; best effort.
        with suppress(Exception):
            await asyncio.wait_for(shared.client.delete(KEY_PREFIX + key), timeout=1)
