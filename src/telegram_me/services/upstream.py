"""Fair admission of requests to t.me (the per-host concurrency cap).

`FairGate` hands out TELEGRAM_MAX_CONCURRENCY slots through the native `Gate`
(see `telegram_me_native` / `rust/.../sched.rs`): free slots go round-robin
over *flows* (a channel, or one feed or previews request), one flow holds at
most three quarters of the slots, background refreshes at most half of them
and only while no user request waits, and a full queue sheds new requests
with `OverloadedError` (503 with Retry-After) instead of queueing them.

The flow and priority of a request come from the `UpstreamJob` in the current
context. Every coalesced flight runs with its own job, so a user who joins a
background refresh still queued for a slot raises it to user priority.
"""

import asyncio
import contextlib
import math
from collections.abc import AsyncIterator, Iterator
from contextvars import ContextVar

import telegram_me_native as native

from telegram_me.telegram.errors import OverloadedError

# Requests allowed to wait for a slot, per slot, unless configured.
QUEUE_PER_SLOT = 16


class UpstreamJob:
    """Flow and priority of the upstream requests made in a context."""

    __slots__ = ("_gate", "_ticket", "background", "flow")

    def __init__(self, *, background: bool = False, flow: str | None = None) -> None:
        self.background = background
        self.flow = flow
        self._gate: FairGate | None = None
        self._ticket: int | None = None

    def child(self, *, background: bool = False) -> "UpstreamJob":
        """A job for work started from this one (it inherits flow and priority)."""
        return UpstreamJob(background=background or self.background, flow=self.flow)

    def promote(self) -> None:
        """Run at user priority from now on, also when already queued for a slot."""
        if not self.background:
            return
        self.background = False
        if self._gate is not None and self._ticket is not None:
            self._gate.promote(self._ticket)

    def wait_for(self, gate: "FairGate | None", ticket: int | None) -> None:
        """Record the queued ticket this job waits on, so `promote` can move it."""
        self._gate, self._ticket = gate, ticket


_job: ContextVar[UpstreamJob | None] = ContextVar("upstream_job", default=None)


def current_job() -> UpstreamJob:
    """The job of the current context (a user request when none is set)."""
    return _job.get() or UpstreamJob()


def set_job(job: UpstreamJob) -> None:
    """Make `job` the current context's job."""
    _job.set(job)


@contextlib.contextmanager
def flow(name: str) -> Iterator[None]:
    """Count the upstream requests started inside the block as one flow."""
    token = _job.set(UpstreamJob(background=current_job().background, flow=name))
    try:
        yield
    finally:
        _job.reset(token)


def run_in_background() -> None:
    """Give the current context background priority (for long-running tasks)."""
    _job.set(UpstreamJob(background=True))


class FairGate:
    """Asynchronous face of the native `Gate`."""

    def __init__(self, capacity: int, max_queue: int | None = None) -> None:
        capacity = max(capacity, 1)
        self.capacity = capacity
        self._gate = native.Gate(
            capacity,
            max(1, capacity * 3 // 4),
            max(1, capacity // 2),
            QUEUE_PER_SLOT * capacity if max_queue is None else max_queue,
        )
        self._waiting: dict[int, asyncio.Future[None]] = {}

    @property
    def active(self) -> int:
        """Slots held now."""
        return self._gate.active

    @property
    def queued(self) -> int:
        """Requests waiting for a slot."""
        return self._gate.queued

    def retry_after(self) -> int:
        """Suggested client back-off in whole seconds."""
        return math.ceil(self._gate.retry_after)

    @contextlib.asynccontextmanager
    async def slot(self, default_flow: str) -> AsyncIterator[None]:
        """Hold one upstream slot for the block."""
        job = current_job()
        try:
            ticket, granted = self._gate.acquire(job.flow or default_flow, job.background)
        except native.Overloaded as exc:
            msg = "Too many requests are waiting for t.me, retry later"
            raise OverloadedError(msg, self.retry_after()) from exc
        try:
            if not granted:
                waiter = asyncio.get_running_loop().create_future()
                self._waiting[ticket] = waiter
                job.wait_for(self, ticket)
                try:
                    await waiter
                finally:
                    job.wait_for(None, None)
                    self._waiting.pop(ticket, None)
            yield
        finally:
            # Held or still queued (the waiter was cancelled): give it back.
            self._grant(self._gate.release(ticket))

    def promote(self, ticket: int) -> None:
        """Raise a queued ticket to user priority."""
        self._grant(self._gate.promote(ticket))

    def _grant(self, tickets: list[int]) -> None:
        pending = list(tickets)
        while pending:
            ticket = pending.pop()
            waiter = self._waiting.pop(ticket, None)
            if waiter is not None and not waiter.done():
                waiter.set_result(None)
            else:
                # Its waiter was cancelled meanwhile; pass the slot on.
                pending.extend(self._gate.release(ticket))
