"""The peer layer: processes on a private network share their work directly.

Optional (`ENABLE_PEERS`), groupcache-style. The work is native
(`telegram_me_native.PeerNode`, Rust on tokio, see `rust/.../peer/`): gossip
membership (SWIM), authenticated frames, rendezvous hashing, and a network
thread that answers other nodes' cache reads from this process's in-process
cache without the interpreter. This module bridges it to asyncio: answers
come back as events, woken through a socket the event loop watches.

Every cache key has one owner among the live nodes: the node that wins the
rendezvous hash of its channel, so all keys of a channel (pages, rendered
bodies and posts, "more" fragments, the preview) live together. On an
in-process miss a node asks the owner (`PeerCache`); to load a key it asks
the owner to load it (`services.lease.PeerLease`), and only the owner fetches
from t.me. Each kind of request has a short deadline (PEER_TIMEOUT); a peer
that is down, slow or refusing never fails a request, the asker does the work
itself (with the Redis lease, when there is Redis).

Nodes find each other through PEERS (addresses or DNS names, resolved again
periodically) and, with Redis, through the heartbeat markers
(`tme:v2:node:*`), which carry each node's peer address.
"""

import asyncio
import contextvars
import json
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import telegram_me_native as native
from redis.exceptions import RedisError

from telegram_me.cache import KEY_PREFIX, NODE_PREFIX, CacheEntry, MemoryCache, RedisCache
from telegram_me.services import upstream
from telegram_me.settings import Settings
from telegram_me.telegram.errors import (
    OverloadedError,
    TelegramError,
    TelegramNotFoundError,
    TelegramParseError,
    TelegramUpstreamError,
    TelegramValidationError,
)

logger = logging.getLogger(__name__)

# Error classes a load answers with; 0 (anything else) means "do it yourself".
_ERRORS: dict[int, type[TelegramError]] = {
    1: TelegramNotFoundError,
    2: TelegramParseError,
    3: TelegramValidationError,
    4: TelegramUpstreamError,
}
_DISCOVERY_INTERVAL = 10.0
# How often refused and rejected peer traffic is reported, at most.
_REPORT_INTERVAL = 30.0
_MAX_DISCOVERED = 256

# Set while this process serves another node's load: it is the owner then,
# and never passes the work on again.
serving_peer: contextvars.ContextVar[bool] = contextvars.ContextVar("serving_peer", default=False)

type Serve = Callable[[str, float], Awaitable[list[str]]]


def route(key: str) -> str:
    """The part of a cache key that decides its owner: the channel."""
    match key.split(":"):
        case ["json", "body", "page", _, channel, *_]:
            return channel
        case ["json", "post", _, channel, *_]:
            return channel
        case ["page" | "more", _, channel, *_]:
            return channel
        case ["preview", channel]:
            return channel
        case _:
            return key


def error_code(exc: BaseException) -> int:
    """The class a load error travels as (0: the asker loads it itself)."""
    if isinstance(exc, OverloadedError):
        # Local overload: the asker may well have room.
        return 0
    for code, error_type in _ERRORS.items():
        if isinstance(exc, error_type):
            return code
    return 0


def error_from(code: int, message: str) -> TelegramError | None:
    """The error a failed load stands for, or None to load it oneself."""
    error_type = _ERRORS.get(code)
    return None if error_type is None else error_type(message)


@dataclass(frozen=True, slots=True)
class Loaded:
    """How a load asked of the owner ended.

    Neither field set means no usable answer: load it yourself.
    """

    entry: CacheEntry | None = None
    error: TelegramError | None = None


class Peers:
    """This process's node of the peer layer, on the event loop."""

    def __init__(self, node: native.PeerNode) -> None:
        self.node = node
        self._pending: dict[int, asyncio.Future[tuple[str, object, object]]] = {}
        self._members: list[str] = []
        self._serve: Serve | None = None
        self._casts: dict[str, Callable[[bytes], None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stats: dict[str, int] = {}

    @classmethod
    def start(cls, settings: Settings, local: MemoryCache, *, refresher: bool) -> "Peers | None":
        """Bind a peer port and join; None (and a warning) when no port is free."""
        if settings.peer_secret is None:
            return None
        try:
            node = native.PeerNode(
                cache=local.native,
                secret=settings.peer_secret.get_secret_value().encode(),
                ports=settings.peer_ports,
                advertise=settings.peer_advertise_addr,
                seeds=settings.peers,
                seed_ports=settings.peer_ports,
                allow=settings.peer_allow_cidr,
                encrypt=settings.peer_encryption,
                refresher=refresher,
                ask_timeout=settings.peer_timeout,
            )
        except OSError as exc:
            logger.warning("Peer layer off in this process: %s", exc)
            return None
        logger.info("Peer layer on: this node is %s", node.addr)
        return cls(node)

    @property
    def addr(self) -> str:
        """This node: `ip:port`."""
        return self.node.addr

    @property
    def members(self) -> list[str]:
        """The other live nodes, as the event loop last heard of them."""
        return self._members

    @property
    def active(self) -> bool:
        """Whether any other node is live (else this process works alone)."""
        return bool(self._members)

    def owner(self, key: str) -> str | None:
        """The node owning `key`; None when it is this one or no other is live."""
        return self.node.owner(route(key)) if self._members else None

    def is_leader(self) -> bool:
        """Whether this process should run the feed refresher."""
        return self.node.is_leader()

    def serve_with(self, serve: Serve) -> None:
        """Load keys other nodes ask this one for with `serve(key, newer_than)`.

        It returns the cache keys to answer with, the requested one first.
        """
        self._serve = serve

    def on_cast(self, topic: str, handler: Callable[[bytes], None]) -> None:
        """Pass messages about `topic` from other nodes to `handler`."""
        self._casts[topic] = handler

    def cast(self, topic: str, data: bytes) -> None:
        """Send `data` about `topic` to every other node, best effort."""
        if self._members:
            self.node.cast(topic, data)

    async def get(self, owner: str, key: str) -> CacheEntry | None:
        """The owner's cached entry for `key`, or None (missing, or no answer).

        A found entry is kept in this process's cache for its remaining TTL.
        """
        _kind, value, stored_at = await self._ask(self.node.get(owner, key))
        if isinstance(value, bytes) and isinstance(stored_at, float):
            return CacheEntry(value, stored_at)
        return None

    async def load(self, owner: str, key: str, newer_than: float, wait: float) -> Loaded:
        """Ask the owner to load `key` unless it has one stored after `newer_than`."""
        background = upstream.current_job().background
        token = self.node.load(owner, key, newer_than, wait, background)
        kind, first, second = await self._ask(token)
        if kind == "value" and isinstance(first, bytes) and isinstance(second, float):
            return Loaded(entry=CacheEntry(first, second))
        if kind == "failed" and isinstance(first, int):
            return Loaded(error=error_from(first, str(second)))
        return Loaded()

    def _ask(self, token: int) -> asyncio.Future[tuple[str, object, object]]:
        # Answers are only handled on this loop, so none can come in between.
        future = asyncio.get_running_loop().create_future()
        self._pending[token] = future
        return future

    @asynccontextmanager
    async def running(self, shared: RedisCache | None = None) -> AsyncIterator[None]:
        """Handle the node's events (and discover nodes through Redis) in the block."""
        loop = asyncio.get_running_loop()
        fd = self.node.fileno()
        loop.add_reader(fd, self._drain)
        background = [asyncio.create_task(self._report(), name="peer-report")]
        if shared is not None:
            background.append(asyncio.create_task(self._discover(shared), name="peer-discovery"))
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            loop.remove_reader(fd)
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self.node.close()
            for future in self._pending.values():
                if not future.done():
                    future.set_result(("unavailable", None, None))
            self._pending.clear()

    def _drain(self) -> None:
        for kind, token, first, second in self.node.poll():
            if kind == "serve" and isinstance(first, str) and isinstance(second, tuple):
                newer_than, background = second
                self._start(self._serving(token, first, float(newer_than), bool(background)))
            elif kind == "cast" and isinstance(first, str) and isinstance(second, bytes):
                if (handler := self._casts.get(first)) is not None:
                    handler(second)
            elif kind == "members":
                self._members_changed()
            elif (future := self._pending.pop(token, None)) is not None and not future.done():
                future.set_result((kind, first, second))

    def _members_changed(self) -> None:
        members = self.node.members()
        if members != self._members:
            logger.info("Peers: %s", ", ".join(members) or "none")
        self._members = members

    async def _report(self) -> None:
        """Warn about traffic the node refused: a misconfiguration is otherwise silent."""
        while True:
            await asyncio.sleep(_REPORT_INTERVAL)
            self.report()

    def report(self) -> None:
        """Log refused and rejected peer traffic since the last report."""
        stats = self.node.stats()
        for counter, cause in (
            ("refused", "from outside PEER_ALLOW_CIDR"),
            ("rejected", "that failed authentication: another PEER_SECRET or PEER_ENCRYPTION?"),
        ):
            if (new := stats[counter] - self._stats.get(counter, 0)) > 0:
                logger.warning("Peer layer %s %d connections or messages %s", counter, new, cause)
        self._stats = stats

    def _start(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serving(self, token: int, key: str, newer_than: float, background: bool) -> None:
        serving_peer.set(True)
        upstream.set_job(upstream.UpstreamJob(background=background))
        if not math.isfinite(newer_than):
            newer_than = -math.inf
        if self._serve is None:
            self.node.serve_failed(token, 0, "")
            return
        try:
            keys = await self._serve(key, newer_than)
        except TelegramError as exc:
            self.node.serve_failed(token, error_code(exc), str(exc))
        except asyncio.CancelledError:
            self.node.serve_failed(token, 0, "")
            raise
        except Exception:
            logger.exception("Serving %s for a peer failed", key)
            self.node.serve_failed(token, 0, "")
        else:
            self.node.served(token, keys)

    async def _discover(self, shared: RedisCache) -> None:
        """Pick other nodes' peer addresses out of their Redis heartbeat markers."""
        pattern = f"{KEY_PREFIX}{NODE_PREFIX}*"
        known: set[str] = set()
        while True:
            try:
                found = await _peer_addresses(shared, pattern)
            except (RedisError, OSError) as exc:
                shared.trip(exc)
            else:
                new = sorted(found - known - {self.addr})
                if new:
                    known.update(new)
                    self.node.add_seeds(new[:_MAX_DISCOVERED])
            await asyncio.sleep(_DISCOVERY_INTERVAL)


async def _peer_addresses(shared: RedisCache, pattern: str) -> set[str]:
    if not shared.available:
        return set()
    keys = [key async for key in shared.client.scan_iter(match=pattern, count=100)]
    if not keys:
        return set()
    found: set[str] = set()
    for marker in await shared.client.mget(keys[:_MAX_DISCOVERED]):
        if marker is None:
            continue
        with suppress(ValueError, TypeError, AttributeError):
            peer = json.loads(marker).get("peer")
            if isinstance(peer, str):
                found.add(peer)
    shared.succeeded()
    return found
