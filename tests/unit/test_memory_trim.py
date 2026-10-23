import asyncio
import platform

import pytest

from telegram_me import memory


def test_trim_is_available_exactly_on_glibc() -> None:
    on_glibc = platform.libc_ver()[0] == "glibc"
    assert (memory.malloc_trim is not None) == on_glibc
    assert memory.trim() is on_glibc


async def test_trimming_memory_calls_malloc_trim_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_trim(pad: int) -> int:
        calls.append(pad)
        return 1

    monkeypatch.setattr(memory, "malloc_trim", fake_trim)
    async with memory.trimming_memory(0.01):
        await asyncio.sleep(0.1)
    count = len(calls)
    assert count >= 2
    assert set(calls) == {0}
    await asyncio.sleep(0.05)
    assert len(calls) == count  # stopped with the block


async def test_trimming_memory_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(memory, "malloc_trim", calls.append)
    async with memory.trimming_memory(0):
        await asyncio.sleep(0.03)
    assert calls == []
