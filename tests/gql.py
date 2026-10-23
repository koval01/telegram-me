"""Helpers for GraphQL tests: full-selection queries built from the schema."""

from typing import Any

from fastapi.testclient import TestClient
from graphql import GraphQLObjectType, build_schema, get_named_type

from telegram_me.api.graphql.schema import build_schema as build_strawberry_schema
from tests.conftest import make_settings

SDL = build_strawberry_schema(make_settings()).as_str()
SCHEMA = build_schema(SDL)

type Shape = dict[str, Shape | None]
"""Requested fields: a nested dict whose leaves are `None`."""


def object_type(name: str) -> GraphQLObjectType:
    type_ = SCHEMA.get_type(name)
    assert isinstance(type_, GraphQLObjectType), name
    return type_


def full_shape(type_name: str) -> Shape:
    """Every field of `type_name`, recursively."""
    shape: Shape = {}
    for name, field in object_type(type_name).fields.items():
        inner = get_named_type(field.type)
        shape[name] = full_shape(inner.name) if isinstance(inner, GraphQLObjectType) else None
    return shape


def selection(shape: Shape) -> str:
    """GraphQL selection set text of `shape`."""
    parts = [name if sub is None else f"{name} {selection(sub)}" for name, sub in shape.items()]
    return "{ " + " ".join(parts) + " }"


def full(type_name: str) -> str:
    """Selection set of every field of `type_name`."""
    return selection(full_shape(type_name))


BODY = (
    "query Body($channel: String!, $position: Int) "
    f"{{ body(channel: $channel, position: $position) {full('ChannelBody')} }}"
)
MORE = (
    "query More($channel: String!, $direction: Direction!, $position: Int!) "
    f"{{ more(channel: $channel, direction: $direction, position: $position) {full('More')} }}"
)
POST = (
    "query Post($channel: String!, $id: Int!) "
    f"{{ post(channel: $channel, id: $id) {full('Post')} }}"
)
PREVIEW = f"query Preview($channel: String!) {{ preview(channel: $channel) {full('Preview')} }}"
PREVIEWS = (
    f"query Previews($channels: [String!]!) {{ previews(channels: $channels) {full('Preview')} }}"
)
FEED = f"query Feed($channels: [String!]!) {{ feed(channels: $channels) {full('FeedPost')} }}"


def execute(
    client: TestClient,
    query: str,
    variables: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """POST one operation and return the decoded GraphQL response."""
    response = client.post("/graphql", json={"query": query, "variables": variables, **extra})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def data(client: TestClient, query: str, variables: dict[str, Any] | None = None) -> Any:
    """`data` of a response that must not contain errors."""
    result = execute(client, query, variables)
    assert "errors" not in result, result["errors"]
    return result["data"]


def project(value: Any, shape: Shape | None) -> Any:
    """The parts of a REST response that a GraphQL selection of `shape` returns."""
    if shape is None or value is None:
        return value
    if isinstance(value, list):
        return [project(item, shape) for item in value]
    return {name: project(value.get(name), sub) for name, sub in shape.items()}


def error_codes(result: dict[str, Any]) -> list[str]:
    """`extensions.code` of every error, in order."""
    return [error["extensions"]["code"] for error in result.get("errors", [])]
