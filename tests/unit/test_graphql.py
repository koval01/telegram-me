"""GraphQL endpoint: errors, limits, feature flags, efficiency and schema drift."""

import asyncio
import gzip
import logging
import types as pytypes
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal, get_args, get_origin

import pytest
from fastapi.testclient import TestClient
from graphql import (
    GraphQLEnumType,
    get_introspection_query,
    get_named_type,
    is_non_null_type,
)
from pydantic import BaseModel

from telegram_me.api.graphql.context import RequestLoader
from telegram_me.api.graphql.schema import _read_key
from telegram_me.app import create_app
from telegram_me.container import build_container
from telegram_me.schemas import channel as channel_models
from telegram_me.schemas import post as post_models
from telegram_me.services.channels import ChannelService
from telegram_me.telegram.client import FetchResult, Method
from tests import gql
from tests.conftest import FIXTURES_DIR, ClientFactory, FixtureFetcher, make_settings

DUROV_LATEST = '{ body(channel: "durov") { channel { username } } }'


@contextmanager
def serve(fetcher: Any, **overrides: Any) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(**overrides), fetcher=fetcher)) as client:
        yield client


class StaticFetcher:
    """Answers every request with the same status and body."""

    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self.text = text

    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        return FetchResult(self.status, self.text)

    async def close(self) -> None:
        return None


class BarrierFetcher(FixtureFetcher):
    """Holds requests until `parties` of them run at the same time."""

    def __init__(self, parties: int) -> None:
        super().__init__()
        self.parties = parties
        self.active = 0
        self.peak = 0
        self._all_arrived = asyncio.Event()

    async def fetch(
        self,
        method: Method,
        path: str,
        *,
        query: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active >= self.parties:
            self._all_arrived.set()
        try:
            # Sequential resolution would time out here.
            await asyncio.wait_for(self._all_arrived.wait(), timeout=2)
            return await super().fetch(method, path, query=query, headers=headers)
        finally:
            self.active -= 1


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES_DIR / f"{name}.txt.gz").read_bytes()).decode()


# Endpoint and feature flags -------------------------------------------------------


def test_graphql_is_enabled_by_default(fetcher: FixtureFetcher) -> None:
    assert make_settings().enable_graphql is True
    with serve(fetcher) as client:
        assert gql.data(client, DUROV_LATEST) == {"body": {"channel": {"username": "durov"}}}


def test_graphql_can_be_disabled(make_client: ClientFactory) -> None:
    client = make_client(enable_graphql=False)
    assert client.post("/graphql", json={"query": DUROV_LATEST}).status_code == 404
    assert client.get("/graphql").status_code == 404


def test_graphiql_and_get_queries_with_docs(client: TestClient) -> None:
    page = client.get("/graphql", headers={"accept": "text/html"})
    assert page.status_code == 200
    assert "GraphiQL" in page.text
    response = client.get("/graphql", params={"query": DUROV_LATEST})
    assert response.status_code == 200
    assert response.json() == {"data": {"body": {"channel": {"username": "durov"}}}}


def test_introspection_with_docs(client: TestClient) -> None:
    # GraphiQL's full introspection query fits the default limits.
    result = gql.data(client, get_introspection_query(descriptions=True))
    names = {type_["name"] for type_ in result["__schema"]["types"]}
    assert {"Query", "ChannelBody", "Post", "FeedPost", "Long"} <= names


def test_docs_disabled_turns_off_ide_get_and_introspection(make_client: ClientFactory) -> None:
    client = make_client(disable_docs=True)
    assert client.get("/graphql", headers={"accept": "text/html"}).status_code != 200
    assert client.get("/graphql", params={"query": DUROV_LATEST}).status_code != 200
    result = gql.execute(client, "{ __schema { types { name } } }")
    assert result["data"] is None
    assert set(gql.error_codes(result)) == {"GRAPHQL_VALIDATION_FAILED"}
    assert "introspection has been disabled" in result["errors"][0]["message"]
    assert gql.data(client, DUROV_LATEST)["body"]["channel"]["username"] == "durov"


def test_graphql_route_is_documented_in_openapi(client: TestClient) -> None:
    assert "/graphql" in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize(
    ("query", "feature"),
    [
        ('{ preview(channel: "durov") { username } }', "previews"),
        ('{ previews(channels: ["durov"]) { username } }', "previews"),
        ('{ feed(channels: ["durov"]) { id } }', "feed"),
    ],
)
def test_disabled_features(make_client: ClientFactory, query: str, feature: str) -> None:
    client = make_client(enable_feed=False, enable_previews=False)
    result = gql.execute(client, query)
    assert gql.error_codes(result) == ["FEATURE_DISABLED"]
    assert result["errors"][0]["message"] == (
        f"Feature '{feature}' is disabled by server configuration"
    )
    assert next(iter(result["data"].values())) is None


def test_feed_is_disabled_by_default(fetcher: FixtureFetcher) -> None:
    defaults = type(make_settings())(_env_file=None)
    with TestClient(create_app(defaults, fetcher=fetcher)) as client:
        result = gql.execute(client, '{ feed(channels: ["durov"]) { id } }')
    assert gql.error_codes(result) == ["FEATURE_DISABLED"]


def test_disabled_feature_does_not_fail_other_fields(make_client: ClientFactory) -> None:
    client = make_client(enable_previews=False)
    result = gql.execute(client, '{ preview(channel: "durov") { username } ' + DUROV_LATEST[1:])
    assert result["data"] == {"preview": None, "body": {"channel": {"username": "durov"}}}
    assert gql.error_codes(result) == ["FEATURE_DISABLED"]


@pytest.mark.parametrize(
    "query",
    [
        '{ preview(channel: "bad-name") { username } }',
        '{ previews(channels: ["bad-name"]) { username } }',
        "{ previews(channels: []) { username } }",
        '{ feed(channels: ["bad-name"]) { id } }',
    ],
)
def test_validation_runs_before_feature_gate(make_client: ClientFactory, query: str) -> None:
    # Same order as REST: invalid input is reported even while a feature is off.
    client = make_client(enable_feed=False, enable_previews=False)
    assert gql.error_codes(gql.execute(client, query)) == ["BAD_INPUT"]


# Errors ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ('{ body(channel: "bad-name") { meta { offset { before } } } }', "Invalid Telegram"),
        ('{ body(channel: "abc") { meta { offset { before } } } }', "Invalid Telegram"),
        ('{ body(channel: "durov", position: 0) { meta { offset { before } } } }', "'position'"),
        ('{ post(channel: "durov", id: 0) { id } }', "'id' must be between 1 and 10000000"),
        ('{ post(channel: "durov", id: 10000001) { id } }', "'id' must be between"),
        ('{ more(channel: "durov", direction: after, position: -1) { posts { id } } }', "'pos"),
        ('{ preview(channel: "1durov") { username } }', "Invalid Telegram"),
        ("{ previews(channels: []) { username } }", "Provide from 1 to 100 channels"),
        ('{ feed(channels: ["durov", "x"]) { id } }', "Invalid Telegram"),
    ],
)
def test_bad_input(client: TestClient, fetcher: FixtureFetcher, query: str, message: str) -> None:
    result = gql.execute(client, query)
    assert gql.error_codes(result) == ["BAD_INPUT"]
    assert message in result["errors"][0]["message"]
    assert fetcher.requests == []


def test_too_many_channels(client: TestClient) -> None:
    channels = [f"channel{i:03}" for i in range(101)]
    result = gql.execute(
        client,
        "query($c: [String!]!) { previews(channels: $c) { username } }",
        {"c": channels},
    )
    assert gql.error_codes(result) == ["BAD_INPUT"]


def test_channel_whitespace_is_trimmed(client: TestClient) -> None:
    assert gql.data(client, '{ body(channel: " durov ") { channel { username } } }') == {
        "body": {"channel": {"username": "durov"}}
    }


def test_not_found(
    client: TestClient, manifest: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    query = "query($c: String!) { body(channel: $c) { channel { username } } }"
    with caplog.at_level(logging.WARNING):
        result = gql.execute(client, query, {"c": manifest["missing"]})
    assert result["data"] == {"body": None}
    assert result["errors"] == [
        {
            "message": "Requested Telegram resource was not found",
            "locations": [{"line": 1, "column": 22}],
            "path": ["body"],
            "extensions": {"code": "NOT_FOUND"},
        }
    ]
    assert not caplog.records  # client errors are not logged


def test_post_missing_from_page() -> None:
    with serve(StaticFetcher(200, _page("post_durov_531"))) as client:
        result = gql.execute(client, '{ post(channel: "durov", id: 999999) { id } }')
    assert result["data"] == {"post": None}
    assert gql.error_codes(result) == ["NOT_FOUND"]
    assert result["errors"][0]["message"] == "Requested post was not found"


def test_upstream_unavailable(
    client: TestClient,
    fetcher: FixtureFetcher,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The app is built by a fixture: building it in the test body would
    # reconfigure logging and detach caplog.
    monkeypatch.setattr(fetcher, "fetch", StaticFetcher(500, "").fetch)
    with caplog.at_level(logging.WARNING):
        result = gql.execute(client, DUROV_LATEST)
    assert result["data"] == {"body": None}
    assert gql.error_codes(result) == ["UNAVAILABLE"]
    assert result["errors"][0]["message"] == "Telegram upstream returned status 500"
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_unparseable_upstream() -> None:
    with serve(StaticFetcher(200, "<not json>")) as client:
        result = gql.execute(
            client, '{ more(channel: "durov", direction: before, position: 5) { posts { id } } }'
        )
    assert gql.error_codes(result) == ["BAD_GATEWAY"]
    assert result["errors"][0]["message"] == "Failed to parse Telegram more response"


def test_unexpected_errors_are_masked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(*_args: Any) -> str:
        msg = "secret internal detail"
        raise RuntimeError(msg)

    monkeypatch.setattr(ChannelService, "body_json", broken)
    with caplog.at_level(logging.ERROR):
        result = gql.execute(client, DUROV_LATEST)
    assert result["data"] == {"body": None}
    assert result["errors"][0]["message"] == "Internal server error"
    assert gql.error_codes(result) == ["INTERNAL_SERVER_ERROR"]
    assert "secret" not in str(result)
    assert any("secret internal detail" in str(record.exc_info) for record in caplog.records)


def test_partial_results(client: TestClient, manifest: dict[str, Any]) -> None:
    query = """query($missing: String!) {
      missing: body(channel: $missing) { channel { username } }
      found: body(channel: "durov") { channel { username } }
    }"""
    result = gql.execute(client, query, {"missing": manifest["missing"]})
    assert result["data"] == {"missing": None, "found": {"channel": {"username": "durov"}}}
    assert [error["path"] for error in result["errors"]] == [["missing"]]


def test_previews_omit_unknown_and_tolerate_partial_failures(
    client: TestClient, manifest: dict[str, Any]
) -> None:
    query = "query($c: [String!]!) { previews(channels: $c) { username } }"
    assert gql.data(client, query, {"c": [manifest["missing"]]}) == {"previews": []}


def test_previews_fail_when_every_channel_fails() -> None:
    with serve(StaticFetcher(500, "")) as client:
        result = gql.execute(client, '{ previews(channels: ["durov", "telegram"]) { username } }')
    assert gql.error_codes(result) == ["UNAVAILABLE"]


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("{ body(", "GRAPHQL_PARSE_FAILED"),
        ("{ nope }", "GRAPHQL_VALIDATION_FAILED"),
        ('{ post(channel: "durov", id: "1") { id } }', "GRAPHQL_VALIDATION_FAILED"),
        ('{ body(channel: "durov") }', "GRAPHQL_VALIDATION_FAILED"),
        ("{ more(channel: \"durov\", direction: sideways, position: 1) { posts { id } } }",
         "GRAPHQL_VALIDATION_FAILED"),
    ],
)  # fmt: skip
def test_document_errors(
    client: TestClient, fetcher: FixtureFetcher, query: str, code: str
) -> None:
    result = gql.execute(client, query)
    assert result.get("data") is None
    assert gql.error_codes(result) == [code]
    assert fetcher.requests == []


def test_mutations_and_subscriptions_are_not_supported(client: TestClient) -> None:
    result = gql.execute(client, "mutation { body }")
    assert gql.error_codes(result) == ["GRAPHQL_VALIDATION_FAILED"]


# Limits ----------------------------------------------------------------------------


def test_depth_limit(make_client: ClientFactory, fetcher: FixtureFetcher) -> None:
    # Depth counts the levels below a root field (the root field is 0).
    client = make_client(graphql_max_depth=3)
    allowed = '{ body(channel: "durov") { meta { offset { before } } } }'
    assert gql.data(client, allowed)["body"]["meta"]["offset"]["before"] is not None
    fetched = len(fetcher.requests)
    too_deep = '{ body(channel: "durov") { content { posts { footer { views } } } } }'
    deeper = '{ post(channel: "durov", id: 531) { content { reply { name { html } } } } }'
    for query in (too_deep, deeper):
        result = gql.execute(client, query)
        assert gql.error_codes(result) == ["GRAPHQL_VALIDATION_FAILED"]
        assert "exceeds maximum operation depth of 3" in result["errors"][0]["message"]
    assert len(fetcher.requests) == fetched


def test_depth_limit_counts_fragments(make_client: ClientFactory) -> None:
    client = make_client(graphql_max_depth=3)
    query = """{ body(channel: "durov") { ...C } }
      fragment C on ChannelBody { content { posts { footer { views } } } }"""
    assert gql.error_codes(gql.execute(client, query)) == ["GRAPHQL_VALIDATION_FAILED"]


def test_alias_limit(make_client: ClientFactory) -> None:
    client = make_client(graphql_max_aliases=2)
    two = '{ a: body(channel: "durov") { meta { offset { before } } } b: __typename }'
    assert "errors" not in gql.execute(client, two)
    three = two.replace("}", "} c: __typename", 1)
    result = gql.execute(client, three)
    assert gql.error_codes(result) == ["GRAPHQL_VALIDATION_FAILED"]
    assert "3 aliases found. Allowed: 2" in result["errors"][0]["message"]


def test_token_limit(make_client: ClientFactory) -> None:
    client = make_client(graphql_max_tokens=10)
    result = gql.execute(client, DUROV_LATEST)
    assert gql.error_codes(result) == ["GRAPHQL_PARSE_FAILED"]
    assert "Document contains more than 10 tokens" in result["errors"][0]["message"]


def test_complexity_limit_boundary(make_client: ClientFactory, fetcher: FixtureFetcher) -> None:
    # 4 fields + 10 for the channel `body` fetches.
    query = '{ body(channel: "durov") { meta { offset { before } } } }'
    assert "errors" not in gql.execute(make_client(graphql_max_complexity=14), query)
    fetched = len(fetcher.requests)
    result = gql.execute(make_client(graphql_max_complexity=13), query)
    assert result == {
        "data": None,
        "errors": [
            {
                "message": "Query cost 14 exceeds the maximum of 13",
                "extensions": {"code": "QUERY_TOO_COMPLEX"},
            }
        ],
    }
    assert len(fetcher.requests) == fetched


def test_complexity_counts_channels_of_list_fields(
    make_client: ClientFactory, fetcher: FixtureFetcher
) -> None:
    channels = [f"channel{i:03}" for i in range(100)]
    one = "query($c: [String!]!) { feed(channels: $c) { id } }"
    two = (
        "query($c: [String!]!) "
        "{ a: feed(channels: $c) { id } b: previews(channels: $c) { username } }"
    )
    # The feature gate answers after the cost check, so nothing is fetched.
    client = make_client(enable_feed=False, enable_previews=False)
    # A full 100-channel feed fits the default limit; two such fields do not.
    assert gql.error_codes(gql.execute(client, one, {"c": channels})) == ["FEATURE_DISABLED"]
    result = gql.execute(client, two, {"c": channels})
    assert gql.error_codes(result) == ["QUERY_TOO_COMPLEX"]
    assert result["errors"][0]["message"] == "Query cost 2004 exceeds the maximum of 1500"
    small = gql.execute(client, two, {"c": channels[:2]})
    assert gql.error_codes(small) == ["FEATURE_DISABLED", "FEATURE_DISABLED"]
    assert fetcher.requests == []


def test_batching(client: TestClient) -> None:
    response = client.post(
        "/graphql",
        json=[
            {"query": DUROV_LATEST},
            {"query": "query($id: Int!) { post(channel: \"durov\", id: $id) { id } }",
             "variables": {"id": 531}},
        ],
    )  # fmt: skip
    assert response.status_code == 200
    assert response.json() == [
        {"data": {"body": {"channel": {"username": "durov"}}}},
        {"data": {"post": {"id": 531}}},
    ]


def test_batch_size_limit(make_client: ClientFactory, fetcher: FixtureFetcher) -> None:
    client = make_client(graphql_max_batch_size=2)
    response = client.post("/graphql", json=[{"query": DUROV_LATEST}] * 3)
    assert response.status_code == 400
    assert "Too many operations" in response.text
    assert fetcher.requests == []


def test_limits_apply_to_each_operation_of_a_batch(make_client: ClientFactory) -> None:
    client = make_client(graphql_max_aliases=1)
    response = client.post(
        "/graphql",
        json=[{"query": "{ a: __typename }"}, {"query": "{ a: __typename b: __typename }"}],
    )
    first, second = response.json()
    assert first == {"data": {"a": "Query"}}
    assert gql.error_codes(second) == ["GRAPHQL_VALIDATION_FAILED"]


# Efficiency ------------------------------------------------------------------------


def test_page_is_fetched_once_per_request(
    make_client: ClientFactory, fetcher: FixtureFetcher
) -> None:
    # With the page cache off, only the per-request memo can share the page.
    client = make_client(cache_mode="off")
    query = """{
      a: body(channel: "durov") { channel { username } }
      b: body(channel: "durov") { meta { offset { before } } }
      ...Again
    }
    fragment Again on Query { body(channel: "durov") { content { posts { id } } } }"""
    result = gql.data(client, query)
    assert set(result) == {"a", "b", "body"}
    assert fetcher.requests == [("GET", "/s/durov")]
    gql.data(client, DUROV_LATEST)
    assert fetcher.requests == [("GET", "/s/durov")] * 2  # memo is per request


def test_batch_operations_share_pages(make_client: ClientFactory, fetcher: FixtureFetcher) -> None:
    client = make_client(cache_mode="off")
    client.post("/graphql", json=[{"query": DUROV_LATEST}, {"query": DUROV_LATEST}])
    assert fetcher.requests == [("GET", "/s/durov")]


def test_concurrent_fields_reading_one_page_share_the_fetch(
    make_client: ClientFactory, fetcher: FixtureFetcher
) -> None:
    client = make_client(cache_mode="off")
    query = """{
      body(channel: "durov", position: 531) { channel { username } }
      post(channel: "durov", id: 531) { id }
    }"""
    assert gql.data(client, query) == {
        "body": {"channel": {"username": "durov"}},
        "post": {"id": 531},
    }
    assert fetcher.requests == [("GET", "/s/durov/531")]


def test_previews_are_fetched_in_parallel() -> None:
    fetcher = BarrierFetcher(parties=3)
    with serve(fetcher) as client:
        result = gql.data(
            client, '{ previews(channels: ["durov", "telegram", "stranaua"]) { username } }'
        )
    assert [entry["username"] for entry in result["previews"]] == ["durov", "telegram", "stranaua"]
    assert fetcher.peak == 3


def test_root_fields_are_resolved_in_parallel() -> None:
    fetcher = BarrierFetcher(parties=2)
    with serve(fetcher) as client:
        query = (
            '{ a: preview(channel: "durov") { username } '
            'b: body(channel: "telegram") { channel { username } } }'
        )
        result = gql.data(client, query)
    assert result == {"a": {"username": "durov"}, "b": {"channel": {"username": "telegram"}}}
    assert fetcher.peak == 2


async def test_loader_shares_results_and_errors(fetcher: FixtureFetcher) -> None:
    async with build_container(make_settings(cache_mode="off"), fetcher) as container:
        loader = RequestLoader(container)
        first, second = await asyncio.gather(loader.preview("durov"), loader.preview("durov"))
        assert first is second
        assert fetcher.requests == [("GET", "/durov")]
        missing = "zzzzzznotexistingchannelzzzz"
        results = await asyncio.gather(
            loader.preview(missing), loader.preview(missing), return_exceptions=True
        )
        assert all(type(result).__name__ == "TelegramNotFoundError" for result in results)
        assert fetcher.requests.count(("GET", f"/{missing}")) == 1


async def test_cancelled_caller_does_not_cancel_shared_call(fetcher: FixtureFetcher) -> None:
    async with build_container(make_settings(cache_mode="off"), fetcher) as container:
        loader = RequestLoader(container)
        cancelled = asyncio.ensure_future(loader.body("durov", None))
        waiting = asyncio.ensure_future(loader.body("durov", None))
        await asyncio.sleep(0)
        cancelled.cancel()
        body = await waiting
        assert body["channel"]["username"] == "durov"
        assert cancelled.cancelled()


# Schema mirrors the REST models -----------------------------------------------------

# REST model -> GraphQL type, and fields only GraphQL has.
MIRRORS: dict[type[BaseModel], tuple[str, frozenset[str]]] = {
    post_models.ParsedAndRaw: ("ParsedAndRaw", frozenset()),
    post_models.Duration: ("Duration", frozenset()),
    post_models.MediaItem: ("MediaItem", frozenset()),
    post_models.TextEntities: ("TextEntity", frozenset()),
    post_models.Text: ("Text", frozenset()),
    post_models.PollOptions: ("PollOption", frozenset()),
    post_models.Poll: ("Poll", frozenset()),
    post_models.Inline: ("InlineButton", frozenset()),
    post_models.Reply: ("Reply", frozenset()),
    post_models.PreviewLink: ("PreviewLink", frozenset()),
    post_models.Reacts: ("Reaction", frozenset()),
    post_models.ContentPost: ("PostContent", frozenset()),
    post_models.Date: ("Date", frozenset()),
    post_models.Footer: ("Footer", frozenset()),
    post_models.Forwarded: ("Forwarded", frozenset()),
    post_models.Post: ("Post", frozenset()),
    channel_models.Counter: ("Counters", frozenset()),
    channel_models.Channel: ("Channel", frozenset()),
    channel_models.OffsetItem: ("Offset", frozenset()),
    channel_models.Meta: ("Meta", frozenset()),
    channel_models.Content: ("ChannelContent", frozenset()),
    channel_models.ChannelBody: ("ChannelBody", frozenset()),
    channel_models.More: ("More", frozenset()),
    channel_models.PreviewChannel: ("PreviewChannel", frozenset()),
    channel_models.Preview: ("Preview", frozenset({"username"})),
}
# Represented differently: a list of `Preview` with `username` instead of a map.
NOT_MIRRORED = {channel_models.Previews}


def _models() -> set[type[BaseModel]]:
    return {
        value
        for module in (post_models, channel_models)
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == module.__name__
    }


def _optional(annotation: Any) -> bool:
    return get_origin(annotation) is pytypes.UnionType and type(None) in get_args(annotation)


def test_every_rest_model_is_mirrored() -> None:
    assert _models() == set(MIRRORS) | NOT_MIRRORED


@pytest.mark.parametrize("model", list(MIRRORS), ids=lambda model: model.__name__)
def test_type_mirrors_model(model: type[BaseModel]) -> None:
    name, extra = MIRRORS[model]
    graphql_fields = gql.object_type(name).fields
    assert set(graphql_fields) == set(model.model_fields) | extra
    for field_name, field in model.model_fields.items():
        graphql_type = graphql_fields[field_name].type
        assert _optional(field.annotation) != is_non_null_type(graphql_type), field_name
        literal = field.annotation
        if get_origin(literal) is Literal:
            enum = get_named_type(graphql_type)
            assert isinstance(enum, GraphQLEnumType)
            assert list(enum.values) == list(get_args(literal)), field_name


def test_feed_post_extends_post() -> None:
    post = gql.object_type("Post").fields
    feed_post = gql.object_type("FeedPost").fields
    assert set(feed_post) == set(post) | {"channel", "score"}
    assert str(feed_post["score"].type) == "Float!"
    assert str(feed_post["channel"].type) == "Channel!"


def test_default_resolver_reads_keys_and_attributes() -> None:
    assert _read_key({"a": 1}, "a") == 1
    assert _read_key({}, "a") is None  # absent keys (raw feed data) are null
    assert _read_key(pytypes.SimpleNamespace(a=2), "a") == 2
