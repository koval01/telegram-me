from collections.abc import Mapping

import pytest
from pyreqwest.exceptions import ConnectError, StatusError

from telegram_me.telegram.client import FetchResult, Method, TelegramClient
from telegram_me.telegram.errors import (
    TelegramNotFoundError,
    TelegramParseError,
    TelegramUpstreamError,
    TelegramValidationError,
)


class ScriptedFetcher:
    """Returns queued results (or raises queued errors) in order."""

    def __init__(self, *outcomes: FetchResult | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str, dict[str, int] | None, dict[str, str] | None]] = []

    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        self.calls.append(
            (method, path, dict(query) if query else None, dict(headers) if headers else None)
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def close(self) -> None:
        return None


def _network_error() -> TelegramUpstreamError:
    error = TelegramUpstreamError("boom")
    error.__cause__ = ConnectError("connection reset", {"causes": None})
    return error


async def test_channel_page_paths() -> None:
    fetcher = ScriptedFetcher(FetchResult(200, "a"), FetchResult(200, "b"))
    client = TelegramClient(fetcher, max_concurrency=2)

    assert await client.channel_page("durov") == "a"
    assert await client.channel_page("durov", 42) == "b"
    assert [call[1] for call in fetcher.calls] == ["/s/durov", "/s/durov/42"]


async def test_large_positions_are_accepted() -> None:
    # Regression: 2.0 accepts every id up to MAX_POST_ID (1.x rejected > 999999).
    fetcher = ScriptedFetcher(FetchResult(200, "ok"))
    assert await TelegramClient(fetcher, 1).channel_page("durov", 1_234_567) == "ok"


async def test_more_page_decodes_json_and_sends_ajax_header() -> None:
    fetcher = ScriptedFetcher(FetchResult(200, '"<div>posts</div>"'))
    html = await TelegramClient(fetcher, 1).more_page("durov", 10, "before")

    assert html == "<div>posts</div>"
    method, path, query, headers = fetcher.calls[0]
    assert (method, path, query) == ("POST", "/s/durov", {"before": 10})
    assert headers == {"x-requested-with": "XMLHttpRequest"}


async def test_more_page_accepts_object_payload() -> None:
    fetcher = ScriptedFetcher(FetchResult(200, '{"html": "<p>x</p>"}'))
    assert await TelegramClient(fetcher, 1).more_page("durov", 10, "after") == "<p>x</p>"


async def test_more_page_invalid_json_is_parse_error() -> None:
    fetcher = ScriptedFetcher(FetchResult(200, "<html>not json"))
    with pytest.raises(TelegramParseError):
        await TelegramClient(fetcher, 1).more_page("durov", 10, "after")


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, TelegramNotFoundError), (429, TelegramUpstreamError), (500, TelegramUpstreamError)],
)
async def test_error_statuses(status: int, error: type[Exception]) -> None:
    fetcher = ScriptedFetcher(FetchResult(status, ""))
    with pytest.raises(error):
        await TelegramClient(fetcher, 1).preview_page("durov")


async def test_redirect_is_returned_as_empty_page() -> None:
    fetcher = ScriptedFetcher(FetchResult(302, ""))
    assert await TelegramClient(fetcher, 1).channel_page("durov") == ""


@pytest.mark.parametrize("channel", ["ab", "bad-name", "1durov", "x" * 40])
async def test_rejects_invalid_channels(channel: str) -> None:
    client = TelegramClient(ScriptedFetcher(), 1)
    with pytest.raises(TelegramValidationError):
        await client.channel_page(channel)


@pytest.mark.parametrize("position", [0, -1, 10**7 + 1])
async def test_rejects_invalid_positions(position: int) -> None:
    client = TelegramClient(ScriptedFetcher(), 1)
    with pytest.raises(TelegramValidationError):
        await client.channel_page("durov", position)


async def test_retries_once_on_network_error() -> None:
    fetcher = ScriptedFetcher(_network_error(), FetchResult(200, "ok"))
    assert await TelegramClient(fetcher, 1).channel_page("durov") == "ok"
    assert len(fetcher.calls) == 2


async def test_does_not_retry_other_errors() -> None:
    error = TelegramUpstreamError("bad")
    error.__cause__ = StatusError("status", {"status": 500, "causes": None})
    fetcher = ScriptedFetcher(error, FetchResult(200, "never"))
    with pytest.raises(TelegramUpstreamError):
        await TelegramClient(fetcher, 1).channel_page("durov")
    assert len(fetcher.calls) == 1


async def test_second_network_error_propagates() -> None:
    fetcher = ScriptedFetcher(_network_error(), _network_error())
    with pytest.raises(TelegramUpstreamError):
        await TelegramClient(fetcher, 1).channel_page("durov")
