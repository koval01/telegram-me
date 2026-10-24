"""Request coalescing ("single flight") for upstream work.

The bookkeeping lives in the native `Flights` table (Rust): which keys are in
flight, and how many callers wait. Both are bounded; a caller beyond a bound
gets `OverloadedError` (503 with Retry-After) instead of queueing without
limit. Several `SingleFlight`s can share one table (and so its bounds) by
using distinct key prefixes.
"""

import asyncio
import contextvars
import functools
import logging
from collections.abc import Awaitable, Callable

import telegram_me_native as native

from telegram_me.services import upstream
from telegram_me.telegram.errors import OverloadedError

logger = logging.getLogger(__name__)

UNBOUNDED = 2**62


def flight_table(max_keys: int = UNBOUNDED, max_waiters: int = UNBOUNDED) -> native.Flights:
    """A table of work in flight for `SingleFlight`s to share."""
    return native.Flights(max_keys, max_waiters)


class _Flight[T]:
    """Shared work: its task and the upstream job it runs as."""

    __slots__ = ("job", "task")

    def __init__(self, task: asyncio.Task[T], job: upstream.UpstreamJob) -> None:
        self.task = task
        self.job = job


class SingleFlight[T]:
    """Run at most one task per key; concurrent callers share its result.

    The shared result object is handed to every caller, so callers must treat
    it as read-only. A caller that is cancelled (for example because its HTTP
    client disconnected) does not cancel the shared task.
    """

    def __init__(self, table: native.Flights | None = None, prefix: str = "") -> None:
        self._table = flight_table() if table is None else table
        self._prefix = prefix

    def __len__(self) -> int:
        return len(self._table)

    def start(
        self, key: str, factory: Callable[[], Awaitable[T]], *, background: bool = False
    ) -> asyncio.Task[T] | None:
        """Return the running task for `key`, starting `factory()` if there is none.

        Fire and forget: the caller does not count as a waiter. `background`
        work runs at background priority upstream. Returns None when the work
        was shed (too many keys in flight).
        """
        key = self._prefix + key
        try:
            flight: _Flight[T] | None = self._table.join(key, False)  # type: ignore[assignment]
        except native.Overloaded:
            logger.debug("Shed background work for %s", key)
            return None
        if flight is None:
            flight = self._lead(key, factory, background=background)
        return flight.task

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        """Await the shared task for `key`."""
        key = self._prefix + key
        try:
            flight: _Flight[T] | None = self._table.join(key, True)  # type: ignore[assignment]
        except native.Overloaded as exc:
            msg = "Too many requests are waiting, retry later"
            raise OverloadedError(msg) from exc
        try:
            if flight is None:
                flight = self._lead(key, factory, background=False)
            else:
                # A user waits for it now, whoever started it.
                flight.job.promote()
            return await asyncio.shield(flight.task)
        finally:
            self._table.leave()

    def _lead(
        self, key: str, factory: Callable[[], Awaitable[T]], *, background: bool
    ) -> _Flight[T]:
        # The task runs as a job of its own: followers can promote it without
        # touching the caller's context.
        job = upstream.current_job().child(background=background)
        context = contextvars.copy_context()
        context.run(upstream.set_job, job)
        task = asyncio.get_running_loop().create_task(_awaited(factory), context=context)
        flight = _Flight(task, job)
        self._table.lead(key, flight)
        task.add_done_callback(functools.partial(self._finish, key, flight))
        return flight

    def _finish(self, key: str, flight: "_Flight[T]", task: asyncio.Task[T]) -> None:
        self._table.finish(key, flight)
        if not task.cancelled() and (exc := task.exception()) is not None:
            # Retrieve the exception so fire-and-forget refreshes do not log
            # "Task exception was never retrieved"; awaiting callers still get it.
            logger.debug("Coalesced task %s failed: %r", key, exc)


async def _awaited[T](factory: Callable[[], Awaitable[T]]) -> T:
    # `factory()` is called inside the task, so its synchronous part runs in
    # the task's context too.
    return await factory()
