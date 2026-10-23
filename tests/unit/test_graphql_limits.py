from typing import Any

import pytest
from graphql import FragmentDefinitionNode, OperationDefinitionNode, parse

from telegram_me.api.graphql.limits import FETCH_COST, operation_cost


def _cost(query: str, variables: dict[str, Any] | None = None) -> int:
    document = parse(query)
    operation = next(
        node for node in document.definitions if isinstance(node, OperationDefinitionNode)
    )
    fragments = {
        node.name.value: node
        for node in document.definitions
        if isinstance(node, FragmentDefinitionNode)
    }
    return operation_cost(operation, fragments, variables)


@pytest.mark.parametrize(
    ("query", "cost"),
    [
        ("{ __typename }", 1),
        ('{ body(channel: "a") { meta { offset { before after } } } }', 5 + FETCH_COST),
        ('{ more(channel: "a", direction: after, position: 1) { posts { id } } }', 3 + FETCH_COST),
        ('{ post(channel: "a", id: 1) { id view } }', 3 + FETCH_COST),
        ('{ preview(channel: "a") { username } }', 2 + FETCH_COST),
        ('{ previews(channels: ["a", "b", "c"]) { username } }', 2 + 3 * FETCH_COST),
        ('{ feed(channels: "a") { id } }', 2 + FETCH_COST),
        ('{ feed(channels: []) { id } }', 2),
        ('{ a: post(channel: "a", id: 1) { id } b: post(channel: "a", id: 1) { id } }',
         2 * (2 + FETCH_COST)),
        ("{ __schema { types { name } } }", 3),
    ],
)  # fmt: skip
def test_cost(query: str, cost: int) -> None:
    assert _cost(query) == cost


def test_nested_fields_are_not_priced_as_fetches() -> None:
    # `post` below the root is a field of `ChannelContent`, not a root fetch.
    assert _cost('{ body(channel: "a") { content { posts { id } } } }') == 4 + FETCH_COST


@pytest.mark.parametrize(
    ("variables", "channels"),
    [
        ({"c": ["a", "b"]}, 2),
        ({"c": []}, 0),
        ({"c": "a"}, 1),  # a single value is coerced to a list
        ({}, 100),  # unknown: priced at the maximum a field accepts
        (None, 100),
    ],
)
def test_cost_of_variables(variables: dict[str, Any] | None, channels: int) -> None:
    query = "query($c: [String!]!) { previews(channels: $c) { username } }"
    assert _cost(query, variables) == 2 + channels * FETCH_COST


def test_fragments_are_expanded() -> None:
    query = """
      query { ...Root }
      fragment Root on Query { body(channel: "a") { ...Meta ... on ChannelBody { __typename } } }
      fragment Meta on ChannelBody { meta { offset { before } } }
    """
    # body + meta + offset + before + __typename, and the fetch of `body`.
    assert _cost(query) == 5 + FETCH_COST


def test_inline_fragments_at_the_root_count_fetches() -> None:
    query = '{ ... on Query { preview(channel: "a") { username } } }'
    assert _cost(query) == 2 + FETCH_COST


def test_fragment_bomb_is_priced_exponentially_without_exponential_work() -> None:
    levels = 30
    fragments = "\n".join(
        f"fragment F{i} on Query {{ ...F{i + 1} ...F{i + 1} }}" for i in range(levels)
    )
    query = f"{{ ...F0 }}\n{fragments}\nfragment F{levels} on Query {{ __typename }}"
    assert _cost(query) == 2**levels


def test_unknown_and_cyclic_fragments_cost_nothing() -> None:
    # Both are rejected by validation; the counter must still terminate.
    assert _cost("{ ...Missing }") == 0
    cyclic = "{ ...A } fragment A on Query { __typename ...B } fragment B on Query { ...A }"
    assert _cost(cyclic) == 1
