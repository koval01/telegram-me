import gzip
import json
import os
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import time_machine
from fastapi.testclient import TestClient

from telegram_me.app import create_app
from telegram_me.settings import Settings
from telegram_me.telegram.client import FetchResult, Method

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures" / "telegram"
SNAPSHOTS_DIR = TESTS_DIR / "snapshots"
MANIFEST: dict[str, Any] = json.loads((FIXTURES_DIR / "manifest.json").read_text("utf-8"))
# Shortly after the fixtures were recorded; feed scoring depends on "now".
FROZEN_NOW = "2026-10-23T00:00:00+00:00"

type Snapshot = Callable[[str, Any], None]
type ClientFactory = Callable[..., TestClient]


def _normalize_path(path: str) -> str:
    """Treat `/s/<channel>`, `/s/<channel>/0` and `/s/<channel>/None` as one page."""
    for suffix in ("/None", "/0"):
        if path.startswith("/s/") and path.endswith(suffix):
            return path.removesuffix(suffix)
    return path


def _key(method: str, path: str, query: Mapping[str, Any] | None) -> tuple[Any, ...]:
    params = tuple(sorted((name, str(value)) for name, value in (query or {}).items()))
    return method, _normalize_path(path), params


class FixtureFetcher:
    """Serves recorded t.me responses; unrecorded requests fail the test."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self._responses = {
            _key(item["method"], item["path"], item["params"]): item
            for item in MANIFEST["responses"]
        }

    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        self.requests.append((method, path))
        item = self._responses.get(_key(method, path, query))
        if item is None:
            pytest.fail(f"Unexpected upstream request in tests: {method} {path} {query}")
        body = gzip.decompress((FIXTURES_DIR / item["file"]).read_bytes()).decode("utf-8")
        return FetchResult(item["status"], body)

    async def close(self) -> None:
        return None


def make_settings(**overrides: Any) -> Settings:
    """Settings isolated from the environment and `.env` files."""
    defaults: dict[str, Any] = {
        "enable_feed": True,
        "enable_previews": True,
        "feed_refresh_mode": "off",
        "redis_url": None,
    }
    return Settings(_env_file=None, **(defaults | overrides))


@pytest.fixture
def fetcher() -> FixtureFetcher:
    return FixtureFetcher()


@pytest.fixture
def make_client(fetcher: FixtureFetcher) -> Iterator[ClientFactory]:
    """Create test clients for apps built with the given settings overrides."""
    clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        client = TestClient(create_app(make_settings(**overrides), fetcher=fetcher))
        client.__enter__()
        clients.append(client)
        return client

    with time_machine.travel(FROZEN_NOW, tick=False):
        yield factory
        for client in clients:
            client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    return make_client()


@pytest.fixture
def manifest() -> dict[str, Any]:
    return MANIFEST


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> Snapshot:
    """Compare data with tests/snapshots/<name>.json (UPDATE_SNAPSHOTS=1 rewrites)."""
    update = os.environ.get("UPDATE_SNAPSHOTS") == "1"

    def check(name: str, data: Any) -> None:
        path = SNAPSHOTS_DIR / f"{name}.json"
        serialized = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(serialized, encoding="utf-8")
            return
        if not path.exists():
            pytest.fail(f"Missing snapshot {path.name}; run with UPDATE_SNAPSHOTS=1")
        expected = json.loads(path.read_text(encoding="utf-8"))
        assert expected == json.loads(serialized), (
            f"Snapshot {path.name} differs (test: {request.node.name})"
        )

    return check
