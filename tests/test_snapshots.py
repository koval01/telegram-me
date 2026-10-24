"""Contract snapshots of every endpoint against recorded t.me pages.

These tests pin the exact JSON the API returns, so refactorings can prove
they did not change the public contract. Intentional changes are made by
re-running with UPDATE_SNAPSHOTS=1 and reviewing the snapshot diff.
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic_core import to_json

from tests.conftest import ClientFactory, RawSnapshot, Snapshot

CHANNELS = ["durov", "telegram", "stranaua", "tginfo", "nebudetafk", "nebudetgg"]
PREVIEW_CHANNELS = ["durov", "telegram", "stranaua"]

type Contract = Callable[[str, Response], None]


def _result(response: Response) -> dict[str, Any]:
    return {"status": response.status_code, "body": response.json()}


@pytest.fixture
def contract(snapshot: Snapshot, raw_snapshot: RawSnapshot) -> Contract:
    """Pin a response both as parsed JSON (readable diffs) and byte for byte."""

    def check(name: str, response: Response) -> None:
        snapshot(name, _result(response))
        raw_snapshot(name, response.content)

    return check


def _post_id(manifest: dict[str, Any], channel: str) -> int:
    post_id: int = manifest["channels"][channel]["post_id"]
    return post_id


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_body(
    make_client: ClientFactory, contract: Contract, channel: str, parser_mode: str
) -> None:
    client = make_client(parser_mode=parser_mode)
    contract(f"body_{channel}_{parser_mode}", client.get(f"/v1/body/{channel}"))


@pytest.mark.parametrize("channel", CHANNELS)
def test_body_with_position(
    client: TestClient, contract: Contract, manifest: dict[str, Any], channel: str
) -> None:
    position = _post_id(manifest, channel)
    response = client.get(f"/v1/body/{channel}", params={"position": position})
    contract(f"body_{channel}_position", response)


@pytest.mark.parametrize("channel", CHANNELS)
def test_post(
    client: TestClient, contract: Contract, manifest: dict[str, Any], channel: str
) -> None:
    post_id = _post_id(manifest, channel)
    contract(f"post_{channel}", client.get(f"/v1/post/{channel}/{post_id}"))


@pytest.mark.parametrize("direction", ["before", "after"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_more(
    client: TestClient,
    contract: Contract,
    manifest: dict[str, Any],
    channel: str,
    direction: str,
) -> None:
    position = _post_id(manifest, channel)
    response = client.get(f"/v1/more/{channel}/{direction}/{position}")
    contract(f"more_{channel}_{direction}", response)


@pytest.mark.parametrize("channel", PREVIEW_CHANNELS)
def test_preview(client: TestClient, contract: Contract, channel: str) -> None:
    contract(f"preview_{channel}", client.get(f"/v1/preview/{channel}"))


def test_previews(client: TestClient, contract: Contract) -> None:
    response = client.post("/v1/previews", json={"channels": PREVIEW_CHANNELS})
    contract("previews", response)


def test_missing_channel(client: TestClient, contract: Contract, manifest: dict[str, Any]) -> None:
    missing = manifest["missing"]
    contract("missing_body", client.get(f"/v1/body/{missing}"))
    contract("missing_preview", client.get(f"/v1/preview/{missing}"))
    response = client.post("/v1/previews", json={"channels": ["durov", missing]})
    contract("missing_in_previews", response)


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
def test_feed(make_client: ClientFactory, contract: Contract, parser_mode: str) -> None:
    client = make_client(parser_mode=parser_mode)
    response = client.post("/v1/feed", json={"channels": CHANNELS})
    contract(f"feed_{parser_mode}", response)


def test_extra_posts(client: TestClient, contract: Contract, manifest: dict[str, Any]) -> None:
    for channel, post_ids in manifest["extra_posts"].items():
        for post_id in post_ids:
            response = client.get(f"/v1/post/{channel}/{post_id}")
            contract(f"post_{channel}_{post_id}", response)
            response = client.get(f"/v1/body/{channel}", params={"position": post_id})
            contract(f"body_{channel}_{post_id}", response)


def _synthetic_post(post_id: int, text: str | None, **extra: Any) -> dict[str, Any]:
    post: dict[str, Any] = {
        "id": post_id,
        "content": {"text": {"string": text, "html": text}} if text is not None else {},
        "footer": {
            "views": "1.2K",
            "edited": False,
            "date": {"string": "2026-10-22T18:00:00+00:00", "unix": 1_792_692_000 - post_id},
        },
        "view": f"post {post_id}",
    }
    for key, value in extra.items():
        if key in {"media", "reacts", "poll"}:
            post["content"][key] = value
        elif key in {"views", "edited"}:
            post["footer"][key] = value
        else:
            post[key] = value
    return post


_STORY = (
    "Министерство обороны сообщило о новых поставках техники в регион, поставки "
    "начнутся уже на следующей неделе и продлятся до конца месяца по плану"
)
# Pages as the parser caches them, with what the feed must tolerate: counters
# under the singular key, no username, no channel at all, posts without footer
# or content, repeated ids, keys the feed overrides, characters JSON escapes.
SYNTHETIC_PAGES: dict[str, dict[str, Any]] = {
    "synth_a": {
        "channel": {"username": "Synth_A", "counters": {"subscribers": "1.2K"}},
        "content": {
            "posts": [
                _synthetic_post(
                    1,
                    _STORY,
                    views="15 400",
                    reacts=[
                        {"type": "emoji", "emoji": "👍", "count": "1.2K"},
                        {"type": "emoji", "emoji": "👎", "count": "20"},
                        {"type": "telegram_stars", "emoji": None, "count": "50"},
                        {"type": "custom_emoji", "emoji_id": "1", "count": "999"},
                    ],
                ),
                _synthetic_post(
                    2,
                    "Погода на выходные: солнечно, без осадков, до двадцати градусов тепла днём",
                    media=[{"type": "image"}, {"type": "image"}, {"type": "video"}],
                    poll={"question": "?", "options": []},
                ),
                _synthetic_post(1, "repeated id, dropped"),
                _synthetic_post(
                    3, 'ctl \u0001 sep \u2028 "quoted" back\\slash / 😀 \u00a0', views="n/a"
                ),
                _synthetic_post(4, "overrides", channel="replaced in place", _score=-1),
            ]
        },
        "meta": {"offset": {"before": 1}},
    },
    "synth_b": {
        "channel": {"username": "synth_b", "counters": {"subscriber": "3M"}},
        "content": {
            "posts": [
                _synthetic_post(10, f"⚡️ Срочно! {_STORY}", views="3.5M", edited=True),
                _synthetic_post(
                    11,
                    "Запуск ракеты с космодрома перенесли, старт ракеты состоится завтра утром",
                    media=[{"type": "gif"}, {"type": "sticker"}, {"type": "roundvideo"}],
                ),
            ]
        },
    },
    "synth_c": {
        "channel": {"title": {"string": "C", "html": "C"}},
        "content": {
            "posts": [
                _synthetic_post(20, "Старт ракеты с космодрома: запуск ракеты назначен на утро"),
                {"id": 21, "view": "no footer, no content"},
                {"id": 22, "content": None, "footer": None, "view": "nulls"},
                _synthetic_post(23, None, views="10\u00a0281"),
            ]
        },
    },
    "synth_d": {"content": {"posts": [_synthetic_post(30, "page without channel info")]}},
    "synth_e": {"channel": {"username": "synth_e"}},
}


def test_feed_synthetic(client: TestClient, contract: Contract) -> None:
    container = client.app.state.container  # type: ignore[attr-defined]
    for name, page in SYNTHETIC_PAGES.items():
        key = container.channels.page_key(name)
        client.portal.call(container.cache.set, key, to_json(page), 60)  # type: ignore[union-attr]
    response = client.post("/v1/feed", json={"channels": [*SYNTHETIC_PAGES, "synth_a"]})
    contract("feed_synthetic", response)
