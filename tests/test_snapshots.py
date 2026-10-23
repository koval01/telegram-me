"""Contract snapshots of every endpoint against recorded t.me pages.

These tests pin the exact JSON the API returns, so refactorings can prove
they did not change the public contract. Intentional changes are made by
re-running with UPDATE_SNAPSHOTS=1 and reviewing the snapshot diff.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.conftest import ClientFactory, Snapshot

CHANNELS = ["durov", "telegram", "stranaua", "tginfo", "nebudetafk", "nebudetgg"]
PREVIEW_CHANNELS = ["durov", "telegram", "stranaua"]


def _result(response: Response) -> dict[str, Any]:
    return {"status": response.status_code, "body": response.json()}


def _post_id(manifest: dict[str, Any], channel: str) -> int:
    post_id: int = manifest["channels"][channel]["post_id"]
    return post_id


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_body(
    make_client: ClientFactory, snapshot: Snapshot, channel: str, parser_mode: str
) -> None:
    client = make_client(parser_mode=parser_mode)
    snapshot(f"body_{channel}_{parser_mode}", _result(client.get(f"/v1/body/{channel}")))


@pytest.mark.parametrize("channel", CHANNELS)
def test_body_with_position(
    client: TestClient, snapshot: Snapshot, manifest: dict[str, Any], channel: str
) -> None:
    position = _post_id(manifest, channel)
    response = client.get(f"/v1/body/{channel}", params={"position": position})
    snapshot(f"body_{channel}_position", _result(response))


@pytest.mark.parametrize("channel", CHANNELS)
def test_post(
    client: TestClient, snapshot: Snapshot, manifest: dict[str, Any], channel: str
) -> None:
    post_id = _post_id(manifest, channel)
    snapshot(f"post_{channel}", _result(client.get(f"/v1/post/{channel}/{post_id}")))


@pytest.mark.parametrize("direction", ["before", "after"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_more(
    client: TestClient,
    snapshot: Snapshot,
    manifest: dict[str, Any],
    channel: str,
    direction: str,
) -> None:
    position = _post_id(manifest, channel)
    response = client.get(f"/v1/more/{channel}/{direction}/{position}")
    snapshot(f"more_{channel}_{direction}", _result(response))


@pytest.mark.parametrize("channel", PREVIEW_CHANNELS)
def test_preview(client: TestClient, snapshot: Snapshot, channel: str) -> None:
    snapshot(f"preview_{channel}", _result(client.get(f"/v1/preview/{channel}")))


def test_previews(client: TestClient, snapshot: Snapshot) -> None:
    response = client.post("/v1/previews", json={"channels": PREVIEW_CHANNELS})
    snapshot("previews", _result(response))


def test_missing_channel(client: TestClient, snapshot: Snapshot, manifest: dict[str, Any]) -> None:
    missing = manifest["missing"]
    snapshot("missing_body", _result(client.get(f"/v1/body/{missing}")))
    snapshot("missing_preview", _result(client.get(f"/v1/preview/{missing}")))
    response = client.post("/v1/previews", json={"channels": ["durov", missing]})
    snapshot("missing_in_previews", _result(response))


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
def test_feed(make_client: ClientFactory, snapshot: Snapshot, parser_mode: str) -> None:
    client = make_client(parser_mode=parser_mode)
    response = client.post("/v1/feed", json={"channels": CHANNELS})
    snapshot(f"feed_{parser_mode}", _result(response))


def test_extra_posts(client: TestClient, snapshot: Snapshot, manifest: dict[str, Any]) -> None:
    for channel, post_id in manifest["extra_posts"].items():
        response = client.get(f"/v1/post/{channel}/{post_id}")
        snapshot(f"post_{channel}_{post_id}", _result(response))
        response = client.get(f"/v1/body/{channel}", params={"position": post_id})
        snapshot(f"body_{channel}_{post_id}", _result(response))
