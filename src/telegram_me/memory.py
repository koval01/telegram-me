"""Give freed heap memory back to the operating system.

Responses are built from large short-lived objects (page HTML, parsed pages,
JSON). glibc's malloc keeps the memory they occupied for reuse instead of
returning it, so after a burst of traffic a worker can sit on tens of MiB
it no longer uses; with a Redis cache under load, ~80 of a worker's 185 MiB
were such free heap. `malloc_trim(0)` releases those pages. Elsewhere (musl,
macOS) this is a no-op.
"""

import asyncio
import ctypes
import logging
import platform
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress

logger = logging.getLogger(__name__)


def _load_malloc_trim() -> Callable[[int], int] | None:
    if platform.libc_ver()[0] != "glibc":
        return None
    try:
        trim: Callable[[int], int] = ctypes.CDLL("libc.so.6").malloc_trim
    except (OSError, AttributeError):  # pragma: no cover - glibc always has it
        return None
    return trim


malloc_trim = _load_malloc_trim()


def trim() -> bool:
    """Release free heap memory now; False when the C library can not."""
    if malloc_trim is None:
        return False
    malloc_trim(0)
    return True


@asynccontextmanager
async def trimming_memory(interval: float) -> AsyncIterator[None]:
    """Trim the heap every `interval` seconds while the block runs (0 disables)."""
    if interval <= 0 or malloc_trim is None:
        yield
        return

    async def loop() -> None:
        while True:
            await asyncio.sleep(interval)
            trim()

    task = asyncio.create_task(loop(), name="malloc-trim")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
