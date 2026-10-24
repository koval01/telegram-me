"""Operation cost (complexity) limit.

Every selected field costs 1 and every root field that may contact t.me costs
`FETCH_COST` per channel it reads, so the limit bounds both the size of the
response and the upstream work a single operation can trigger. Fragments are
expanded (and memoized, so fragment "bombs" are counted without blowing up).

The cost is computed before execution, with the operation's variables, so
`previews(channels: $list)` is priced by the actual list length.
"""

from collections.abc import Iterator, Mapping
from typing import Any, override

from graphql import (
    ExecutionResult,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    InlineFragmentNode,
    ListValueNode,
    OperationDefinitionNode,
    SelectionSetNode,
    VariableNode,
)
from strawberry.extensions import SchemaExtension

from telegram_me.api.graphql.errors import ErrorCode
from telegram_me.validation import MAX_CHANNELS_PER_REQUEST

FETCH_COST = 10
"""Cost of every channel a root field may fetch from t.me."""

# Root fields that read one channel, and those reading a `channels` list.
_SINGLE_CHANNEL_FIELDS = frozenset({"body", "more", "post", "preview"})
_MULTI_CHANNEL_FIELDS = frozenset({"previews", "feed"})


class _CostCounter:
    def __init__(
        self,
        fragments: Mapping[str, FragmentDefinitionNode],
        variables: Mapping[str, Any],
    ) -> None:
        self._fragments = fragments
        self._variables = variables
        self._memo: dict[tuple[str, bool], int] = {}
        self._visiting: set[str] = set()

    def selection_set(self, node: SelectionSetNode | None, *, root: bool) -> int:
        if node is None:
            return 0
        cost = 0
        for selection in node.selections:
            if isinstance(selection, FieldNode):
                cost += 1 + self.selection_set(selection.selection_set, root=False)
                if root:
                    cost += FETCH_COST * self._channels(selection)
            elif isinstance(selection, InlineFragmentNode):
                cost += self.selection_set(selection.selection_set, root=root)
            elif isinstance(selection, FragmentSpreadNode):
                cost += self._fragment(selection.name.value, root=root)
        return cost

    def _fragment(self, name: str, *, root: bool) -> int:
        key = (name, root)
        if key in self._memo:
            return self._memo[key]
        fragment = self._fragments.get(name)
        # Unknown and cyclic fragments are rejected by validation already.
        if fragment is None or name in self._visiting:
            return 0
        self._visiting.add(name)
        cost = self.selection_set(fragment.selection_set, root=root)
        self._visiting.discard(name)
        self._memo[key] = cost
        return cost

    def _channels(self, field: FieldNode) -> int:
        name = field.name.value
        if name in _SINGLE_CHANNEL_FIELDS:
            return 1
        argument = next(
            (arg for arg in field.arguments or () if arg.name.value == "channels"), None
        )
        if name not in _MULTI_CHANNEL_FIELDS or argument is None:
            return 0
        value = argument.value
        if isinstance(value, VariableNode):
            # An unknown variable is priced at the most channels a field accepts.
            variable = self._variables.get(value.name.value, [None] * MAX_CHANNELS_PER_REQUEST)
            return len(variable) if isinstance(variable, list) else 1
        if isinstance(value, ListValueNode):
            return len(value.values)
        return 1  # a single value is coerced to a one-element list


def operation_cost(
    operation: OperationDefinitionNode,
    fragments: Mapping[str, FragmentDefinitionNode],
    variables: Mapping[str, Any] | None = None,
) -> int:
    """Cost of `operation` (see the module docstring)."""
    counter = _CostCounter(fragments, variables or {})
    return counter.selection_set(operation.selection_set, root=True)


class MaxComplexityLimiter(SchemaExtension):
    """Reject operations costing more than `max_complexity` before they execute."""

    def __init__(self, max_complexity: int) -> None:
        self.max_complexity = max_complexity

    @override
    def on_execute(self) -> Iterator[None]:
        context = self.execution_context
        document = context.graphql_document
        if document is not None and context.result is None:
            fragments = {
                definition.name.value: definition
                for definition in document.definitions
                if isinstance(definition, FragmentDefinitionNode)
            }
            operations = [
                definition
                for definition in document.definitions
                if isinstance(definition, OperationDefinitionNode)
                and (
                    context.operation_name is None
                    or (definition.name and definition.name.value == context.operation_name)
                )
            ]
            # Validation guarantees exactly one operation runs; price it.
            cost = max(
                (operation_cost(op, fragments, context.variables) for op in operations), default=0
            )
            if cost > self.max_complexity:
                error = GraphQLError(
                    f"Query cost {cost} exceeds the maximum of {self.max_complexity}",
                    extensions={"code": ErrorCode.QUERY_TOO_COMPLEX.value},
                )
                context.result = ExecutionResult(data=None, errors=[error])
        yield
