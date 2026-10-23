"""Application wiring: embedded refresher and logging."""

import json
import logging
from typing import TYPE_CHECKING

import pytest

from telegram_me.log import JsonFormatter, configure_logging
from tests.conftest import ClientFactory

if TYPE_CHECKING:
    from telegram_me.container import Container


def test_embedded_refresher_runs_with_the_app(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks: list[int] = []

    async def tick(_self: object) -> int:
        ticks.append(1)
        return 0

    monkeypatch.setattr("telegram_me.services.feed.FeedRefresher.tick", tick)
    client = make_client(feed_refresh_mode="embedded")
    container: Container = client.app.state.container  # type: ignore[attr-defined]
    assert container.settings.feed_refresh_mode == "embedded"
    assert client.get("/healthz").status_code == 200
    client.__exit__(None, None, None)  # stops the refresher cleanly
    assert ticks


def test_refresher_not_started_when_feed_disabled(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object) -> None:
        pytest.fail("refresher must not start")

    monkeypatch.setattr("telegram_me.container.running_refresher", fail)
    make_client(enable_feed=False, feed_refresh_mode="embedded")
    make_client(enable_feed=True, feed_refresh_mode="worker")


def test_json_formatter_includes_extra_fields() -> None:
    record = logging.makeLogRecord(
        {"name": "x", "levelname": "WARNING", "msg": "post %s", "args": (1,), "data_post": "c/1"}
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "post 1"
    assert payload["level"] == "WARNING"
    assert payload["data_post"] == "c/1"


def test_configure_logging_formats() -> None:
    configure_logging("DEBUG", "json")
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
    configure_logging("INFO", "text")
    assert logging.getLogger().level == logging.INFO
