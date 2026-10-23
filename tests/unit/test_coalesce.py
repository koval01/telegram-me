import asyncio

import pytest

from telegram_me.services.coalesce import SingleFlight


async def test_concurrent_calls_share_one_execution() -> None:
    flight: SingleFlight[int] = SingleFlight()
    calls = 0
    release = asyncio.Event()

    async def work() -> int:
        nonlocal calls
        calls += 1
        await release.wait()
        return 42

    waiters = [asyncio.create_task(flight.run("k", work)) for _ in range(10)]
    await asyncio.sleep(0)
    assert len(flight) == 1
    release.set()
    assert await asyncio.gather(*waiters) == [42] * 10
    assert calls == 1
    assert len(flight) == 0


async def test_new_call_after_completion_runs_again() -> None:
    flight: SingleFlight[int] = SingleFlight()
    calls = 0

    async def work() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await flight.run("k", work) == 1
    assert await flight.run("k", work) == 2


async def test_errors_reach_every_waiter() -> None:
    flight: SingleFlight[int] = SingleFlight()

    async def fail() -> int:
        await asyncio.sleep(0)
        msg = "upstream"
        raise RuntimeError(msg)

    results = await asyncio.gather(
        *(flight.run("k", fail) for _ in range(3)), return_exceptions=True
    )
    assert all(isinstance(result, RuntimeError) for result in results)


async def test_cancelled_waiter_does_not_cancel_shared_work() -> None:
    flight: SingleFlight[str] = SingleFlight()
    release = asyncio.Event()

    async def work() -> str:
        await release.wait()
        return "done"

    first = asyncio.create_task(flight.run("k", work))
    second = asyncio.create_task(flight.run("k", work))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == "done"


async def test_background_start_failure_is_not_reported_as_unretrieved() -> None:
    flight: SingleFlight[int] = SingleFlight()
    loop = asyncio.get_running_loop()
    unhandled: list[object] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

    async def fail() -> int:
        msg = "boom"
        raise RuntimeError(msg)

    task = flight.start("k", fail)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert task.done()
    del task
    assert unhandled == []
