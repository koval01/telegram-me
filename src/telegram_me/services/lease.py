"""One upstream fetch per key across workers and servers: a Redis lease.

`SingleFlight` coalesces identical requests inside one worker process. Every
granian worker of every server has its own, so without shared coordination a
hot key that expires is fetched once per worker per server. Before fetching,
the worker that leads locally takes a short-lived lease in Redis
(`SET NX PX`, in the same round trip as reading the cached value):

- the cached value is already fresh enough (another server just wrote it):
  use it, no fetch;
- the lease is taken: fetch, write the cache, release the lease;
- another worker holds it: poll (with jittered back-off, re-trying the lease
  on every round) until its result is in the cache, then use that. A holder
  that failed leaves its error in the lease for a moment, so waiters fail the
  same way instead of all retrying t.me at once.

The lease lives a few seconds and its holder extends it while the fetch runs
(a heartbeat), so a holder that crashed or lost Redis is taken over within
one lease TTL, however slow t.me is for a live one. Nothing waits for Redis
longer than REDIS_TIMEOUT per command, and no waiter waits longer than
`max_wait` in all: when Redis is slow, down (circuit breaker open) or the
holder is stuck, a worker fetches on its own. At most one worker per process
gets there (the local single flight still applies), so a Redis outage costs
what coalescing per process costs, never a herd.
"""

import asyncio
import inspect
import logging
import random
import secrets
from collections.abc import Awaitable, Callable
from typing import Protocol

from redis.client import NEVER_DECODE
from redis.exceptions import RedisError

from telegram_me.cache import KEY_PREFIX, CacheEntry, MemoryCache, RedisCache, decode_entry
from telegram_me.telegram.errors import (
    OverloadedError,
    TelegramError,
    TelegramNotFoundError,
    TelegramParseError,
    TelegramUpstreamError,
    TelegramValidationError,
)

logger = logging.getLogger(__name__)

LEASE_PREFIX = KEY_PREFIX + "lease:"
# How long a failed holder's error answers waiters (and new arrivals).
ERROR_TTL = 2.0
_FIRST_POLL = 0.025
_MAX_POLL = 0.25

# Errors shared with waiters, by marker; a marker value starts with "!".
_SHARED_ERRORS: dict[str, type[TelegramError]] = {
    "nf": TelegramNotFoundError,
    "pa": TelegramParseError,
    "va": TelegramValidationError,
    "up": TelegramUpstreamError,
}

# KEYS: lease, value. ARGV: token, lease ms, "1" to read the value even while
# another worker holds the lease. Returns {acquired, holder, value, value pttl}.
_ACQUIRE = """
local acquired = redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2]) and 1 or 0
local holder = redis.call('GET', KEYS[1])
local value, pttl = false, -2
if acquired == 1 or ARGV[3] == '1' or not holder then
  value = redis.call('GET', KEYS[2])
  pttl = redis.call('PTTL', KEYS[2])
end
return {acquired, holder, value, pttl}
"""

# KEYS: lease. ARGV: token, lease ms. Extends the lease while its holder runs.
_EXTEND = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

# KEYS: lease. ARGV: token, outcome ("" deletes), outcome ms. Only the holder
# may end its lease: one that outlived its TTL may belong to another worker.
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  if ARGV[2] == '' then return redis.call('DEL', KEYS[1]) end
  return redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
end
return 0
"""

type Adopt[T] = Callable[[CacheEntry], T | Awaitable[T]]


class Lease(Protocol):
    """Runs a fetch at most once at a time per key across processes."""

    async def run[T](
        self,
        key: str,
        fetch: Callable[[], Awaitable[T]],
        adopt: Adopt[T],
        fresh: Callable[[CacheEntry], bool],
    ) -> T:
        """`fetch()`, or `adopt()` the value cached under `key` once it is `fresh`."""
        ...


class LocalLease:
    """No shared state: every process fetches for itself."""

    async def run[T](  # noqa: D102
        self,
        key: str,  # noqa: ARG002 - the Lease protocol
        fetch: Callable[[], Awaitable[T]],
        adopt: Adopt[T],  # noqa: ARG002
        fresh: Callable[[CacheEntry], bool],  # noqa: ARG002
    ) -> T:
        return await fetch()


class RedisLease:
    """`Lease` held in Redis (see the module docstring)."""

    def __init__(
        self,
        shared: RedisCache,
        ttl: float,
        max_wait: float,
        local: MemoryCache | None = None,
        max_local_ttl: float = 0.0,
    ) -> None:
        self._shared = shared
        self._ttl = ttl
        self._ttl_ms = str(int(ttl * 1000))
        self._max_wait = max_wait
        self._local = local
        self._max_local_ttl = max_local_ttl
        self._releases: set[asyncio.Task[None]] = set()
        self.stats = {"fetched": 0, "adopted": 0, "waited": 0, "fallback": 0}

    async def run[T](  # noqa: D102
        self,
        key: str,
        fetch: Callable[[], Awaitable[T]],
        adopt: Adopt[T],
        fresh: Callable[[CacheEntry], bool],
    ) -> T:
        if not self._shared.available:
            return await self._fallback(fetch)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._max_wait
        token = secrets.token_hex(8)
        delay = _FIRST_POLL
        first = True
        while True:
            try:
                acquired, holder, entry, pttl = await self._acquire(key, token, read=first)
            except (RedisError, OSError) as exc:
                self._shared.trip(exc)
                return await self._fallback(fetch)
            if entry is not None and fresh(entry):
                if acquired:
                    self._release(key, token, "")
                self._keep_local(key, entry, pttl)
                self.stats["adopted"] += 1
                value = adopt(entry)
                return await value if inspect.isawaitable(value) else value
            if acquired:
                return await self._hold(key, token, fetch)
            if holder is not None and holder.startswith("!"):
                raise _shared_error(holder)
            if first:
                self.stats["waited"] += 1
                first = False
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.info("Waited %.0fs for the lease of %s; fetching", self._max_wait, key)
                return await self._fallback(fetch)
            await asyncio.sleep(min(delay * random.uniform(0.5, 1.5), remaining))  # noqa: S311
            delay = min(delay * 1.6, _MAX_POLL)

    async def _acquire(
        self, key: str, token: str, *, read: bool
    ) -> tuple[bool, str | None, CacheEntry | None, int]:
        acquired, holder, raw, pttl = await self._shared.client.execute_command(  # type: ignore[no-untyped-call]
            "EVAL",
            _ACQUIRE,
            2,
            LEASE_PREFIX + key,
            KEY_PREFIX + key,
            token,
            self._ttl_ms,
            "1" if read else "0",
            **{NEVER_DECODE: True},
        )
        return (
            bool(acquired),
            holder.decode() if isinstance(holder, bytes) else None,
            decode_entry(raw),
            pttl if isinstance(pttl, int) else -2,
        )

    async def _hold[T](self, key: str, token: str, fetch: Callable[[], Awaitable[T]]) -> T:
        self.stats["fetched"] += 1
        done = asyncio.Event()
        self._track(self._heartbeat(key, token, done))
        try:
            result = await fetch()
        except OverloadedError:
            # Local overload: another worker may well have room.
            self._release(key, token, "")
            raise
        except TelegramError as exc:
            self._release(key, token, _marker(exc))
            raise
        except BaseException:
            self._release(key, token, "")
            raise
        finally:
            done.set()
        self._release(key, token, "")
        return result

    async def _heartbeat(self, key: str, token: str, done: asyncio.Event) -> None:
        """Extend the lease every third of its TTL until `done` (or it is lost)."""
        while True:
            try:
                await asyncio.wait_for(done.wait(), self._ttl / 3)
            except TimeoutError:
                pass
            else:
                return
            try:
                extended = await self._shared.client.eval(
                    _EXTEND, 1, LEASE_PREFIX + key, token, self._ttl_ms
                )
            except (RedisError, OSError) as exc:
                self._shared.trip(exc)
                return
            if not extended:
                return

    async def _fallback[T](self, fetch: Callable[[], Awaitable[T]]) -> T:
        self.stats["fallback"] += 1
        return await fetch()

    def _keep_local(self, key: str, entry: CacheEntry, pttl: int) -> None:
        """Keep a value another process wrote, like a read through the cache would."""
        if self._local is None:
            return
        ttl = self._max_local_ttl if pttl <= 0 else min(pttl / 1000, self._max_local_ttl)
        self._local.fill(key, entry.value, ttl, entry.stored_at)

    def _release(self, key: str, token: str, outcome: str) -> None:
        # Fire and forget: waiters poll, the caller need not wait for Redis.
        self._track(self._end(key, token, outcome))

    def _track(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._releases.add(task)
        task.add_done_callback(self._releases.discard)

    async def _end(self, key: str, token: str, outcome: str) -> None:
        try:
            await self._shared.client.eval(
                _RELEASE, 1, LEASE_PREFIX + key, token, outcome, str(int(ERROR_TTL * 1000))
            )
        except (RedisError, OSError) as exc:
            # The lease expires on its own; waiters then fetch themselves.
            self._shared.trip(exc)

    async def drain(self) -> None:
        """Wait for pending lease releases and heartbeats (for tests and shutdown)."""
        while self._releases:
            await asyncio.gather(*self._releases, return_exceptions=True)


def _marker(exc: TelegramError) -> str:
    for code, error_type in _SHARED_ERRORS.items():
        if isinstance(exc, error_type):
            return f"!{code} {exc}"
    return ""


def _shared_error(marker: str) -> TelegramError:
    code, _, message = marker[1:].partition(" ")
    return _SHARED_ERRORS.get(code, TelegramUpstreamError)(message)
