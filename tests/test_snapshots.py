"""Contract snapshots of every endpoint against recorded t.me pages.

These tests pin the exact JSON the API returns, so refactorings can prove
they did not change the public contract. Intentional changes are made by
re-running with UPDATE_SNAPSHOTS=1 and reviewing the snapshot diff.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import time_machine
from fastapi.testclient import TestClient

from app.main import app
from app.routes.v1 import telegram_routes
from app.utils.config import settings

CHANNELS = ["durov", "telegram", "stranaua", "tginfo", "nebudetafk"]
PREVIEW_CHANNELS = ["durov", "telegram", "stranaua"]
# Shortly after the fixtures were recorded; feed scoring depends on "now".
FROZEN_NOW = "2026-10-23T00:00:00+00:00"

Snapshot = Callable[[str, Any], None]


class FakeRedis:
    """Minimal async stand-in for the preview cache."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.data[key] = value


@pytest.fixture
def client(
    telegram_fixtures: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "ENABLE_FEED", True)
    monkeypatch.setattr(settings, "ENABLE_PREVIEWS", True)
    monkeypatch.setattr(settings, "PARSER_MODE", "full")
    monkeypatch.setattr(telegram_routes.telegram, "_redis_client", FakeRedis())
    with time_machine.travel(FROZEN_NOW, tick=False), TestClient(app) as test_client:
        yield test_client


def _result(response: Any) -> dict[str, Any]:
    return {"status": response.status_code, "body": response.json()}


def _post_id(manifest: dict[str, Any], channel: str) -> int:
    return manifest["channels"][channel]["post_id"]


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_body(
    client: TestClient, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch,
    channel: str, parser_mode: str,
) -> None:
    monkeypatch.setattr(settings, "PARSER_MODE", parser_mode)
    snapshot(f"body_{channel}_{parser_mode}", _result(client.get(f"/v1/body/{channel}")))


@pytest.mark.parametrize("channel", CHANNELS)
def test_body_with_position(
    client: TestClient, snapshot: Snapshot, telegram_fixtures: dict[str, Any], channel: str
) -> None:
    position = _post_id(telegram_fixtures, channel)
    response = client.get(f"/v1/body/{channel}", params={"position": position})
    snapshot(f"body_{channel}_position", _result(response))


@pytest.mark.parametrize("channel", CHANNELS)
def test_post(
    client: TestClient, snapshot: Snapshot, telegram_fixtures: dict[str, Any], channel: str
) -> None:
    post_id = _post_id(telegram_fixtures, channel)
    snapshot(f"post_{channel}", _result(client.get(f"/v1/post/{channel}/{post_id}")))


@pytest.mark.parametrize("direction", ["before", "after"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_more(
    client: TestClient, snapshot: Snapshot, telegram_fixtures: dict[str, Any],
    channel: str, direction: str,
) -> None:
    position = _post_id(telegram_fixtures, channel)
    response = client.get(f"/v1/more/{channel}/{direction}/{position}")
    snapshot(f"more_{channel}_{direction}", _result(response))


@pytest.mark.parametrize("channel", PREVIEW_CHANNELS)
def test_preview(client: TestClient, snapshot: Snapshot, channel: str) -> None:
    snapshot(f"preview_{channel}", _result(client.get(f"/v1/preview/{channel}")))


def test_previews(client: TestClient, snapshot: Snapshot) -> None:
    response = client.post("/v1/previews", json={"channels": PREVIEW_CHANNELS})
    snapshot("previews", _result(response))


def test_missing_channel(
    client: TestClient, snapshot: Snapshot, telegram_fixtures: dict[str, Any]
) -> None:
    missing = telegram_fixtures["missing"]
    snapshot("missing_body", _result(client.get(f"/v1/body/{missing}")))
    snapshot("missing_preview", _result(client.get(f"/v1/preview/{missing}")))
    response = client.post("/v1/previews", json={"channels": ["durov", missing]})
    snapshot("missing_in_previews", _result(response))


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
def test_feed(
    client: TestClient, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch, parser_mode: str
) -> None:
    monkeypatch.setattr(settings, "PARSER_MODE", parser_mode)
    response = client.post("/v1/feed", json={"channels": CHANNELS})
    snapshot(f"feed_{parser_mode}", _result(response))
