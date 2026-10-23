"""Request coalescing ("single flight") for upstream work."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class SingleFlight[T]:
    """Run at most one task per key; concurrent callers share its result.

    The shared result object is handed to every caller, so callers must treat
    it as read-only. A caller that is cancelled (for example because its HTTP
    client disconnected) does not cancel the shared task.
    """

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[T]] = {}

    def __len__(self) -> int:
        return len(self._inflight)

    def start(self, key: str, factory: Callable[[], Awaitable[T]]) -> asyncio.Task[T]:
        """Return the running task for `key`, starting `factory()` if there is none."""
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._finish(key, done))
        return task

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        """Await the shared task for `key`."""
        return await asyncio.shield(self.start(key, factory))

    def _finish(self, key: str, task: asyncio.Task[T]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled() and (exc := task.exception()) is not None:
            # Retrieve the exception so fire-and-forget refreshes do not log
            # "Task exception was never retrieved"; awaiting callers still get it.
            logger.debug("Coalesced task %s failed: %r", key, exc)
