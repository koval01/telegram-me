"""Stable `extensions.code` values for GraphQL errors.

Resolvers raise the same domain errors as the REST endpoints; `ErrorCodes`
rewrites them into GraphQL errors carrying a machine-readable code, the
GraphQL counterpart of the REST status code. Unexpected exceptions are masked
so internals never leak to clients.
"""

import logging
from collections.abc import Iterator
from enum import StrEnum
from typing import Any, override

from graphql import GraphQLError
from strawberry.extensions import SchemaExtension

from telegram_me.api.errors import FeatureDisabledError
from telegram_me.telegram.errors import (
    TelegramError,
    TelegramNotFoundError,
    TelegramParseError,
    TelegramUpstreamError,
    TelegramValidationError,
)

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Values of `extensions.code`."""

    BAD_INPUT = "BAD_INPUT"
    """Invalid argument (REST `422`)."""
    NOT_FOUND = "NOT_FOUND"
    """Channel or post does not exist (REST `404`)."""
    BAD_GATEWAY = "BAD_GATEWAY"
    """t.me answered with a page the parser could not interpret (REST `502`)."""
    UNAVAILABLE = "UNAVAILABLE"
    """t.me is unreachable or answered with an error (REST `503`)."""
    FEATURE_DISABLED = "FEATURE_DISABLED"
    """The field is switched off by server configuration (REST `503`)."""
    QUERY_TOO_COMPLEX = "QUERY_TOO_COMPLEX"
    """The operation costs more than `GRAPHQL_MAX_COMPLEXITY`."""
    GRAPHQL_PARSE_FAILED = "GRAPHQL_PARSE_FAILED"
    """The document is not valid GraphQL syntax (or has too many tokens)."""
    GRAPHQL_VALIDATION_FAILED = "GRAPHQL_VALIDATION_FAILED"
    """The document does not match the schema or exceeds a static limit."""
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    """Unexpected server error; details are logged, not returned."""


# Order matters: the first matching type wins (subclasses before TelegramError).
_CODES: tuple[tuple[type[Exception], ErrorCode], ...] = (
    (FeatureDisabledError, ErrorCode.FEATURE_DISABLED),
    (TelegramValidationError, ErrorCode.BAD_INPUT),
    (TelegramNotFoundError, ErrorCode.NOT_FOUND),
    (TelegramParseError, ErrorCode.BAD_GATEWAY),
    (TelegramUpstreamError, ErrorCode.UNAVAILABLE),
    (TelegramError, ErrorCode.BAD_GATEWAY),
)
_UPSTREAM_FAILURES = frozenset({ErrorCode.BAD_GATEWAY, ErrorCode.UNAVAILABLE})
_INTERNAL_MESSAGE = "Internal server error"


def code_for(exc: BaseException) -> ErrorCode | None:
    """The code of a domain error, `None` for unexpected exceptions."""
    for error_type, code in _CODES:
        if isinstance(exc, error_type):
            return code
    return None


def log_error(error: GraphQLError) -> None:
    """Log a resolver error the way the REST error handlers do.

    Client errors (bad input, not found, disabled features) are not logged,
    upstream failures are warnings and unexpected exceptions are errors.
    Errors raised before execution (syntax, validation) are client errors.
    """
    original = error.original_error
    if original is None or isinstance(original, GraphQLError):
        return
    code = code_for(original)
    if code is None:
        logger.error("Unhandled error in GraphQL resolver", exc_info=original)
    elif code in _UPSTREAM_FAILURES:
        logger.warning("%s: %s", type(original).__name__, original, exc_info=original.__cause__)


def _with_code(error: GraphQLError, code: ErrorCode, message: str | None = None) -> GraphQLError:
    return GraphQLError(
        message if message is not None else error.message,
        nodes=error.nodes,
        source=error.source,
        positions=error.positions,
        path=error.path,
        extensions={**(error.extensions or {}), "code": code.value},
    )


def _coded(error: GraphQLError) -> GraphQLError:
    original = error.original_error
    if original is None or isinstance(original, GraphQLError):
        return error
    code = code_for(original)
    if code is None:
        return _with_code(error, ErrorCode.INTERNAL_SERVER_ERROR, _INTERNAL_MESSAGE)
    return _with_code(error, code)


def _tag(errors: list[GraphQLError] | None, code: ErrorCode) -> None:
    # Pre-execution errors are returned as the same objects, so tag in place.
    for error in errors or ():
        if not (error.extensions or {}).get("code"):
            error.extensions = {**(error.extensions or {}), "code": code.value}


class ErrorCodes(SchemaExtension):
    """Attach `extensions.code` to every error and mask unexpected ones."""

    @override
    def on_parse(self) -> Iterator[None]:
        yield
        _tag(self.execution_context.pre_execution_errors, ErrorCode.GRAPHQL_PARSE_FAILED)

    @override
    def on_validate(self) -> Iterator[None]:
        yield
        _tag(self.execution_context.pre_execution_errors, ErrorCode.GRAPHQL_VALIDATION_FAILED)

    @override
    def on_execute(self) -> Iterator[None]:
        yield
        result: Any = self.execution_context.result
        if result is not None and result.errors:
            result.errors = [_coded(error) for error in result.errors]
