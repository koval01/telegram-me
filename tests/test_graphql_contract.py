"""Contract of the GraphQL API against recorded t.me pages.

* Snapshots pin the schema (SDL) and full-selection responses of every root
  field, like `test_snapshots.py` does for REST (UPDATE_SNAPSHOTS=1 rewrites).
* Parity tests prove that GraphQL returns exactly the REST data, for every
  recorded channel and parser mode, so both APIs can not drift apart.
* Selection tests prove only the requested fields are returned.
"""

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests import gql
from tests.conftest import SNAPSHOTS_DIR, ClientFactory, Snapshot
from tests.test_snapshots import CHANNELS, PREVIEW_CHANNELS

SDL_SNAPSHOT = SNAPSHOTS_DIR / "graphql" / "schema.graphql"


def _post_id(manifest: dict[str, Any], channel: str) -> int:
    post_id: int = manifest["channels"][channel]["post_id"]
    return post_id


def _extra_posts(manifest: dict[str, Any]) -> list[tuple[str, int]]:
    return [
        (channel, post_id)
        for channel, post_ids in manifest["extra_posts"].items()
        for post_id in post_ids
    ]


def _without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


# Schema ----------------------------------------------------------------------------


def test_schema_sdl() -> None:
    """The whole schema: names, types, nullability, arguments and descriptions."""
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SDL_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SDL_SNAPSHOT.write_text(gql.SDL + "\n", encoding="utf-8")
    assert SDL_SNAPSHOT.read_text(encoding="utf-8") == gql.SDL + "\n", (
        "GraphQL schema changed; review and run with UPDATE_SNAPSHOTS=1"
    )


def test_served_schema_matches_sdl(client: TestClient) -> None:
    result = gql.data(client, "{ __schema { queryType { name } types { name } } }")
    served = {type_["name"] for type_ in result["__schema"]["types"]}
    assert result["__schema"]["queryType"] == {"name": "Query"}
    assert served == set(gql.SCHEMA.type_map)


# Snapshots ---------------------------------------------------------------------------


# Full pages are large; parity below covers every channel, these pin the shape.
@pytest.mark.parametrize("channel", ["durov", "nebudetgg"])
def test_body_snapshot(client: TestClient, snapshot: Snapshot, channel: str) -> None:
    result = gql.execute(client, gql.BODY, {"channel": channel})
    snapshot(f"graphql/body_{channel}", result)


def test_body_simplified_snapshot(make_client: ClientFactory, snapshot: Snapshot) -> None:
    client = make_client(parser_mode="simplified")
    snapshot(
        "graphql/body_nebudetgg_simplified", gql.execute(client, gql.BODY, {"channel": "nebudetgg"})
    )


def test_body_position_snapshot(
    client: TestClient, snapshot: Snapshot, manifest: dict[str, Any]
) -> None:
    variables = {"channel": "durov", "position": _post_id(manifest, "durov")}
    snapshot("graphql/body_durov_position", gql.execute(client, gql.BODY, variables))


@pytest.mark.parametrize("channel", CHANNELS)
def test_post_snapshot(
    client: TestClient, snapshot: Snapshot, manifest: dict[str, Any], channel: str
) -> None:
    variables = {"channel": channel, "id": _post_id(manifest, channel)}
    snapshot(f"graphql/post_{channel}", gql.execute(client, gql.POST, variables))


def test_extra_posts_snapshot(
    client: TestClient, snapshot: Snapshot, manifest: dict[str, Any]
) -> None:
    for channel, post_id in _extra_posts(manifest):
        result = gql.execute(client, gql.POST, {"channel": channel, "id": post_id})
        snapshot(f"graphql/post_{channel}_{post_id}", result)


@pytest.mark.parametrize("direction", ["before", "after"])
@pytest.mark.parametrize("channel", ["durov", "nebudetgg"])
def test_more_snapshot(
    client: TestClient,
    snapshot: Snapshot,
    manifest: dict[str, Any],
    channel: str,
    direction: str,
) -> None:
    variables = {
        "channel": channel,
        "direction": direction,
        "position": _post_id(manifest, channel),
    }
    snapshot(f"graphql/more_{channel}_{direction}", gql.execute(client, gql.MORE, variables))


@pytest.mark.parametrize("channel", PREVIEW_CHANNELS)
def test_preview_snapshot(client: TestClient, snapshot: Snapshot, channel: str) -> None:
    snapshot(f"graphql/preview_{channel}", gql.execute(client, gql.PREVIEW, {"channel": channel}))


def test_previews_snapshot(
    client: TestClient, snapshot: Snapshot, manifest: dict[str, Any]
) -> None:
    channels = [*PREVIEW_CHANNELS, manifest["missing"]]
    snapshot("graphql/previews", gql.execute(client, gql.PREVIEWS, {"channels": channels}))


def test_feed_snapshot(client: TestClient, snapshot: Snapshot) -> None:
    result = gql.execute(client, gql.FEED, {"channels": ["durov", "nebudetgg"]})
    snapshot("graphql/feed", result)


def test_missing_snapshot(client: TestClient, snapshot: Snapshot, manifest: dict[str, Any]) -> None:
    missing = manifest["missing"]
    query = """
        query Missing($missing: String!) {
          body(channel: $missing) { channel { username } }
          preview(channel: $missing) { username }
          durov: preview(channel: "durov") { channel { title } }
        }
    """
    snapshot("graphql/missing", gql.execute(client, query, {"missing": missing}))


# Parity with REST ----------------------------------------------------------------------


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_body_parity(make_client: ClientFactory, channel: str, parser_mode: str) -> None:
    client = make_client(parser_mode=parser_mode)
    rest = client.get(f"/v1/body/{channel}").json()
    assert gql.data(client, gql.BODY, {"channel": channel})["body"] == rest


@pytest.mark.parametrize("channel", CHANNELS)
def test_body_position_parity(client: TestClient, manifest: dict[str, Any], channel: str) -> None:
    position = _post_id(manifest, channel)
    rest = client.get(f"/v1/body/{channel}", params={"position": position}).json()
    variables = {"channel": channel, "position": position}
    assert gql.data(client, gql.BODY, variables)["body"] == rest


@pytest.mark.parametrize("channel", CHANNELS)
def test_post_parity(client: TestClient, manifest: dict[str, Any], channel: str) -> None:
    post_id = _post_id(manifest, channel)
    rest = client.get(f"/v1/post/{channel}/{post_id}").json()
    assert gql.data(client, gql.POST, {"channel": channel, "id": post_id})["post"] == rest


def test_extra_posts_parity(client: TestClient, manifest: dict[str, Any]) -> None:
    for channel, post_id in _extra_posts(manifest):
        rest = client.get(f"/v1/post/{channel}/{post_id}").json()
        result = gql.data(client, gql.POST, {"channel": channel, "id": post_id})
        assert result["post"] == rest, (channel, post_id)


@pytest.mark.parametrize("direction", ["before", "after"])
@pytest.mark.parametrize("channel", CHANNELS)
def test_more_parity(
    client: TestClient, manifest: dict[str, Any], channel: str, direction: str
) -> None:
    position = _post_id(manifest, channel)
    rest = client.get(f"/v1/more/{channel}/{direction}/{position}").json()
    variables = {"channel": channel, "direction": direction, "position": position}
    assert gql.data(client, gql.MORE, variables)["more"] == rest


@pytest.mark.parametrize("channel", PREVIEW_CHANNELS)
def test_preview_parity(client: TestClient, channel: str) -> None:
    rest = client.get(f"/v1/preview/{channel}").json()
    assert gql.data(client, gql.PREVIEW, {"channel": channel})["preview"] == {
        "username": channel,
        **rest,
    }


def test_previews_parity(client: TestClient, manifest: dict[str, Any]) -> None:
    channels = [*PREVIEW_CHANNELS, manifest["missing"], "durov"]
    rest = client.post("/v1/previews", json={"channels": channels}).json()
    result = gql.data(client, gql.PREVIEWS, {"channels": channels})["previews"]
    # Same entries in the same (first-requested) order; unknown channels omitted.
    assert result == [{"username": name, **preview} for name, preview in rest.items()]
    assert [entry["username"] for entry in result] == PREVIEW_CHANNELS


@pytest.mark.parametrize("parser_mode", ["full", "simplified"])
def test_feed_parity(make_client: ClientFactory, parser_mode: str) -> None:
    client = make_client(parser_mode=parser_mode)
    rest = client.post("/v1/feed", json={"channels": CHANNELS}).json()["result"]
    result = gql.data(client, gql.FEED, {"channels": CHANNELS})["feed"]
    assert len(result) == len(rest)
    # The REST feed serves raw parsed posts (absent keys instead of nulls) and
    # prefixes the ranking fields with `_`; otherwise the data is identical, in order.
    renamed = {"_score": "score", "_duplicates": "duplicates", "_topic": "topic"}
    expected = [
        {"duplicates": [], **{renamed.get(key, key): value for key, value in post.items()}}
        for post in _without_none(rest)
    ]
    assert _without_none(result) == expected


# Field selection ---------------------------------------------------------------------

SELECTIONS: list[tuple[str, str, dict[str, Any], gql.Shape]] = [
    ("body", "/v1/body/durov", {"channel": "durov"}, {"meta": {"offset": {"before": None}}}),
    (
        "body",
        "/v1/body/nebudetgg",
        {"channel": "nebudetgg"},
        {
            "channel": {"title": {"string": None}, "counters": {"subscribers": None}},
            "content": {"posts": {"id": None, "footer": {"date": {"unix": None}}}},
        },
    ),
    (
        "body",
        "/v1/body/nebudetgg",
        {"channel": "nebudetgg"},
        {
            "content": {
                "posts": {
                    "content": {
                        "media": {"type": None, "duration": {"raw": None}},
                        "poll": {"question": None, "options": {"name": None}},
                        "reacts": {"type": None, "count": None},
                        "reply": {"to_message": None},
                    },
                    "forwarded": {"name": {"string": None}},
                }
            }
        },
    ),
    ("post", "/v1/post/durov/531", {"channel": "durov", "id": 531}, {"id": None}),
    (
        "post",
        "/v1/post/tginfo/4422",
        {"channel": "tginfo", "id": 4422},
        {"content": {"text": {"entities": {"type": None, "url": None}}}, "view": None},
    ),
    (
        "more",
        "/v1/more/telegram/after/441",
        {"channel": "telegram", "direction": "after", "position": 441},
        {"posts": {"id": None}, "meta": {"offset": {"after": None, "before": None}}},
    ),
    (
        "preview",
        "/v1/preview/telegram",
        {"channel": "telegram"},
        {"channel": {"subscribers": None, "is_verified": None}},
    ),
]

_ARG_TYPES = {
    "channel": "String!",
    "id": "Int!",
    "position": "Int!",
    "direction": "Direction!",
}


@pytest.mark.parametrize(("field", "path", "variables", "shape"), SELECTIONS)
def test_only_requested_fields(
    client: TestClient, field: str, path: str, variables: dict[str, Any], shape: gql.Shape
) -> None:
    params = ", ".join(f"${name}: {_ARG_TYPES[name]}" for name in variables)
    args = ", ".join(f"{name}: ${name}" for name in variables)
    query = f"query({params}) {{ {field}({args}) {gql.selection(shape)} }}"
    result = gql.data(client, query, variables)
    # Exactly the requested keys, with the REST values.
    assert result == {field: gql.project(client.get(path).json(), shape)}


def test_aliases_and_typename(client: TestClient) -> None:
    query = """{
      latest: body(channel: "durov") { __typename meta { offset { before } } }
      single: post(channel: "durov", id: 531) { identifier: id views: footer { views } }
    }"""
    result = gql.data(client, query)
    assert set(result) == {"latest", "single"}
    assert set(result["latest"]) == {"__typename", "meta"}
    assert result["latest"]["__typename"] == "ChannelBody"
    assert result["single"]["identifier"] == 531
    assert set(result["single"]["views"]) == {"views"}


def test_previews_selection(client: TestClient) -> None:
    query = '{ previews(channels: ["durov", "telegram"]) { username } }'
    assert gql.data(client, query) == {
        "previews": [{"username": "durov"}, {"username": "telegram"}]
    }


def test_feed_selection(client: TestClient) -> None:
    query = '{ feed(channels: ["durov"]) { id score } }'
    feed = gql.data(client, query)["feed"]
    assert feed
    assert all(set(post) == {"id", "score"} for post in feed)
    # Diversity re-ranking may reorder by score; the order is the REST order.
    rest = client.post("/v1/feed", json={"channels": ["durov"]}).json()["result"]
    assert feed == [{"id": post["id"], "score": post["_score"]} for post in rest]
