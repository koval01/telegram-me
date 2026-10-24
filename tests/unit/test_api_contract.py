"""scripts/api_contract.py: which contract changes count as breaking."""

import copy
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    path = ROOT / "scripts" / "api_contract.py"
    spec = importlib.util.spec_from_file_location("api_contract", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


contract = _load()
OPENAPI: dict[str, Any] = json.loads((ROOT / contract.OPENAPI).read_text("utf-8"))
SDL = (ROOT / contract.GRAPHQL).read_text("utf-8")

type Change = Callable[[dict[str, Any]], None]


def schemas(doc: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = doc["components"]["schemas"]
    return result


def diff(change: Change) -> Any:
    new = copy.deepcopy(OPENAPI)
    change(new)
    return contract.OpenApiDiff(OPENAPI, new).run()


def test_unchanged_document_has_no_changes() -> None:
    report = contract.OpenApiDiff(OPENAPI, copy.deepcopy(OPENAPI)).run()
    assert (report.breaking, report.dangerous) == ([], [])


def _remove_operation(doc: dict[str, Any]) -> None:
    del doc["paths"]["/v1/feed"]["post"]


def _remove_response_field(doc: dict[str, Any]) -> None:
    del schemas(doc)["Post"]["properties"]["view"]


def _response_field_optional(doc: dict[str, Any]) -> None:
    schemas(doc)["Post"]["required"].remove("view")


def _response_field_nullable(doc: dict[str, Any]) -> None:
    schemas(doc)["Post"]["properties"]["id"] = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def _response_field_retyped(doc: dict[str, Any]) -> None:
    schemas(doc)["Post"]["properties"]["id"] = {"type": "string"}


def _nested_response_field_removed(doc: dict[str, Any]) -> None:
    del schemas(doc)["Date"]["properties"]["unix"]


def _new_required_request_field(doc: dict[str, Any]) -> None:
    request = schemas(doc)["FeedRequest"]
    request["properties"]["limit"] = {"type": "integer"}
    request["required"].append("limit")


def _new_required_query_parameter(doc: dict[str, Any]) -> None:
    doc["paths"]["/v1/body/{channel}"]["get"]["parameters"].append(
        {"in": "query", "name": "lang", "required": True, "schema": {"type": "string"}}
    )


def _request_enum_value_removed(doc: dict[str, Any]) -> None:
    parameters = doc["paths"]["/v1/more/{channel}/{direction}/{position}"]["get"]["parameters"]
    parameters[1]["schema"]["enum"] = ["after"]


def _stricter_request_limit(doc: dict[str, Any]) -> None:
    schemas(doc)["ChannelsRequest"]["properties"]["channels"]["maxItems"] = 50


def _success_status_removed(doc: dict[str, Any]) -> None:
    responses = doc["paths"]["/healthz"]["get"]["responses"]
    responses["201"] = responses.pop("200")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (_remove_operation, "POST /v1/feed: operation removed"),
        (_remove_response_field, ".view: removed"),
        (_response_field_optional, ".view: may now be missing"),
        (_response_field_nullable, ".id: can now be null"),
        (_response_field_retyped, ".id: can now be string"),
        (_nested_response_field_removed, ".date.unix: removed"),
        (_new_required_request_field, "request body.limit: new and required"),
        (_new_required_query_parameter, "query parameter `lang`: new and required"),
        (_request_enum_value_removed, 'no longer accepts "before"'),
        (_stricter_request_limit, "maxItems 100 -> 50"),
        (_success_status_removed, "GET /healthz: response 200: removed"),
    ],
    ids=lambda value: getattr(value, "__name__", "")[1:] or None,
)
def test_breaking_change_is_found(change: Change, expected: str) -> None:
    breaking = diff(change).breaking
    assert any(expected in line for line in breaking), breaking


def _new_optional_response_field(doc: dict[str, Any]) -> None:
    schemas(doc)["Post"]["properties"]["pinned"] = {"type": "boolean"}


def _new_optional_request_field(doc: dict[str, Any]) -> None:
    schemas(doc)["FeedRequest"]["properties"]["limit"] = {"type": "integer"}


def _new_operation(doc: dict[str, Any]) -> None:
    doc["paths"]["/v1/stats"] = {"get": {"responses": {"200": {"description": "ok"}}}}


def _wider_request_limit(doc: dict[str, Any]) -> None:
    schemas(doc)["ChannelsRequest"]["properties"]["channels"]["maxItems"] = 200


def _response_field_now_always_set(doc: dict[str, Any]) -> None:
    schemas(doc)["Footer"]["properties"]["views"] = {"type": "string"}
    schemas(doc)["Footer"]["required"].append("views")


@pytest.mark.parametrize(
    "change",
    [
        _new_optional_response_field,
        _new_optional_request_field,
        _new_operation,
        _wider_request_limit,
        _response_field_now_always_set,
    ],
)
def test_compatible_change_is_not_breaking(change: Change) -> None:
    assert diff(change).breaking == []


def test_new_response_enum_value_is_reported_as_dangerous() -> None:
    def add_direction(doc: dict[str, Any]) -> None:
        schemas(doc)["MediaItem"]["properties"]["type"] = {"enum": ["photo", "video", "x"]}

    def media(doc: dict[str, Any]) -> None:
        schemas(doc)["MediaItem"]["properties"]["type"] = {"enum": ["photo", "video"]}

    old = copy.deepcopy(OPENAPI)
    media(old)
    new = copy.deepcopy(OPENAPI)
    add_direction(new)
    report = contract.OpenApiDiff(old, new).run()
    assert report.breaking == []
    assert any('new value "x"' in line for line in report.dangerous)


def test_graphql_removed_field_is_breaking() -> None:
    new = SDL.replace("  username: String!\n", "", 1)
    assert new != SDL
    report = contract.graphql_diff(SDL, new)
    assert any("username was removed" in line for line in report.breaking), report.breaking


def test_graphql_new_field_is_compatible() -> None:
    new = SDL.replace("type Query {\n", "type Query {\n  version: String\n", 1)
    assert new != SDL
    assert contract.graphql_diff(SDL, new).breaking == []
