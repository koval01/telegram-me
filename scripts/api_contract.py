#!/usr/bin/env python3
"""Find breaking changes of the public API against another commit.

Every merge to `main` deploys production, so the base of a pull request is
the contract clients use right now. This compares the contract files at
`--base` with the working tree:

- `tests/snapshots/openapi.json`, the REST API (OpenAPI);
- `tests/snapshots/graphql/schema.graphql`, the GraphQL schema (SDL).

The snapshot tests keep both files equal to what the code serves, so a
changed contract is always a changed file here.

    uv run python scripts/api_contract.py --base origin/main

Breaking means an existing client can fail: an operation, field or enum value
it relies on is gone, a response value can now have a type it does not expect
(including `null`), or a request it sends is no longer accepted (a new
required parameter or field, a narrower type). Additions are fine. Exit code
1 when anything breaks, unless `--allow-breaking` (the pull request is
labelled `breaking-change`: a major version, announced in the changelog).
"""

from __future__ import annotations

import argparse
import json
import operator
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPENAPI = "tests/snapshots/openapi.json"
GRAPHQL = "tests/snapshots/graphql/schema.graphql"

type Schema = dict[str, Any]


@dataclass
class Report:
    """Breaking changes and changes worth a look (dangerous)."""

    breaking: list[str] = field(default_factory=list)
    dangerous: list[str] = field(default_factory=list)


class OpenApiDiff:
    """Compares two OpenAPI 3.1 documents as produced by FastAPI."""

    def __init__(self, old: Schema, new: Schema) -> None:
        self.old, self.new = old, new
        self.report = Report()
        self._seen: set[tuple[str, str, str]] = set()

    def run(self) -> Report:
        """Compare every operation of the old document with the new one."""
        for path, old_ops in self.old.get("paths", {}).items():
            new_ops = self.new.get("paths", {}).get(path, {})
            for method, old_op in old_ops.items():
                where = f"{method.upper()} {path}"
                if method not in new_ops:
                    self.report.breaking.append(f"{where}: operation removed")
                    continue
                self._operation(where, old_op, new_ops[method])
        return self.report

    def _operation(self, where: str, old: Schema, new: Schema) -> None:
        def params(op: Schema) -> dict[tuple[str, str], Schema]:
            return {(p["name"], p["in"]): p for p in op.get("parameters", [])}

        old_params, new_params = params(old), params(new)
        for key, param in new_params.items():
            name = f"{where}: {key[1]} parameter `{key[0]}`"
            before = old_params.get(key)
            if before is None:
                if param.get("required"):
                    self.report.breaking.append(f"{name}: new and required")
                continue
            if param.get("required") and not before.get("required"):
                self.report.breaking.append(f"{name}: now required")
            self._schema(name, before.get("schema", {}), param.get("schema", {}), request=True)

        old_body, new_body = old.get("requestBody"), new.get("requestBody")
        if new_body is not None:
            if new_body.get("required") and not (old_body or {}).get("required"):
                self.report.breaking.append(f"{where}: request body now required")
            if old_body is not None:
                self._schema(
                    f"{where}: request body",
                    _json_schema(old_body),
                    _json_schema(new_body),
                    request=True,
                )

        new_responses = new.get("responses", {})
        for status, response in old.get("responses", {}).items():
            name = f"{where}: response {status}"
            if status not in new_responses:
                if status.startswith("2"):
                    self.report.breaking.append(f"{name}: removed")
                continue
            self._schema(
                name, _json_schema(response), _json_schema(new_responses[status]), request=False
            )

    def _resolve(self, doc: Schema, schema: Schema) -> tuple[Schema, str]:
        name = ""
        while "$ref" in schema:
            name = schema["$ref"]
            parts = name.removeprefix("#/").split("/")
            target: Any = doc
            for part in parts:
                target = target[part]
            schema = target
        if len(schema.get("allOf", [])) == 1 and len(schema) <= 3:
            # FastAPI wraps a referenced model with a description in allOf.
            inner, inner_name = self._resolve(doc, schema["allOf"][0])
            return inner, inner_name or name
        return schema, name

    def _variants(self, doc: Schema, schema: Schema) -> list[tuple[Schema, str]]:
        schema, name = self._resolve(doc, schema)
        alternatives = schema.get("anyOf") or schema.get("oneOf")
        if not alternatives:
            return [(schema, name)]
        out: list[tuple[Schema, str]] = []
        for alternative in alternatives:
            out.extend(self._variants(doc, alternative))
        return out

    def _types(self, doc: Schema, schema: Schema) -> set[str]:
        types: set[str] = set()
        for variant, _ in self._variants(doc, schema):
            types |= _own_types(variant)
        if "number" in types:
            types.add("integer")
        return types

    def _schema(self, where: str, old: Schema, new: Schema, *, request: bool) -> None:
        old_types, new_types = self._types(self.old, old), self._types(self.new, new)
        if "any" not in old_types and "any" not in new_types:
            if request:
                if lost := old_types - new_types:
                    self.report.breaking.append(f"{where}: no longer accepts {_names(lost)}")
            elif gained := new_types - old_types:
                self.report.breaking.append(f"{where}: can now be {_names(gained)}")

        old_variants = [v for v in self._variants(self.old, old) if _own_types(v[0]) != {"null"}]
        new_variants = [v for v in self._variants(self.new, new) if _own_types(v[0]) != {"null"}]
        self._enums(where, old_variants, new_variants, request=request)
        if len(old_variants) == 1 and len(new_variants) == 1:
            self._shape(where, old_variants[0], new_variants[0], request=request)
            return
        old_named = {name: variant for variant, name in old_variants if name}
        new_named = {name: variant for variant, name in new_variants if name}
        if request:
            for name in old_named.keys() - new_named.keys():
                self.report.breaking.append(f"{where}: no longer accepts {_short(name)}")
        else:
            for name in new_named.keys() - old_named.keys():
                self.report.breaking.append(f"{where}: can now be {_short(name)}")
        for name in old_named.keys() & new_named.keys():
            pair = ((old_named[name], name), (new_named[name], name))
            self._shape(where, *pair, request=request)

    def _enums(
        self,
        where: str,
        old: list[tuple[Schema, str]],
        new: list[tuple[Schema, str]],
        *,
        request: bool,
    ) -> None:
        def values(variants: list[tuple[Schema, str]]) -> set[Any] | None:
            found: set[Any] = set()
            for variant, _ in variants:
                if "enum" in variant:
                    found |= {json.dumps(v) for v in variant["enum"]}
                elif "const" in variant:
                    found.add(json.dumps(variant["const"]))
                else:
                    return None
            return found

        old_values, new_values = values(old), values(new)
        if old_values is None or new_values is None:
            return
        if request and (lost := old_values - new_values):
            self.report.breaking.append(f"{where}: no longer accepts {', '.join(sorted(lost))}")
        if not request and (gained := new_values - old_values):
            self.report.dangerous.append(f"{where}: new value {', '.join(sorted(gained))}")

    def _shape(
        self,
        where: str,
        old: tuple[Schema, str],
        new: tuple[Schema, str],
        *,
        request: bool,
    ) -> None:
        (old_schema, old_name), (new_schema, new_name) = old, new
        key = (old_name, new_name, "request" if request else "response")
        if old_name and new_name:
            if key in self._seen:
                return
            self._seen.add(key)

        if request:
            self._bounds(where, old_schema, new_schema)
        if "items" in old_schema and "items" in new_schema:
            self._schema(f"{where}[]", old_schema["items"], new_schema["items"], request=request)

        old_props: Schema = old_schema.get("properties", {})
        new_props: Schema = new_schema.get("properties", {})
        old_required = set(old_schema.get("required", []))
        new_required = set(new_schema.get("required", []))
        for prop, schema in old_props.items():
            name = f"{where}.{prop}"
            if prop not in new_props:
                if not request:
                    self.report.breaking.append(f"{name}: removed")
                elif new_schema.get("additionalProperties") is False:
                    self.report.breaking.append(f"{name}: no longer accepted")
                continue
            if not request and prop in old_required and prop not in new_required:
                self.report.breaking.append(f"{name}: may now be missing")
            self._schema(name, schema, new_props[prop], request=request)
        if request:
            for prop in sorted(new_required - old_required):
                self.report.breaking.append(f"{where}.{prop}: new and required")

    def _bounds(self, where: str, old: Schema, new: Schema) -> None:
        """Validation of what clients send must not get stricter."""
        for names, stricter in ((_UPPER_BOUNDS, operator.lt), (_LOWER_BOUNDS, operator.gt)):
            for name in names:
                if name in new and (name not in old or stricter(new[name], old[name])):
                    before = old.get(name, "none")
                    self.report.breaking.append(f"{where}: {name} {before} -> {new[name]}")
        if old.get("pattern") != new.get("pattern") and "pattern" in new:
            self.report.dangerous.append(
                f"{where}: pattern {old.get('pattern')!r} -> {new['pattern']!r}"
            )


_UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
_LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")


def _json_schema(container: Schema) -> Schema:
    schema: Schema = container.get("content", {}).get("application/json", {}).get("schema", {})
    return schema


def _own_types(schema: Schema) -> set[str]:
    if "const" in schema:
        return {_json_type(schema["const"])}
    if "enum" in schema:
        return {_json_type(value) for value in schema["enum"]}
    declared = schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return set(declared)
    if "properties" in schema or "additionalProperties" in schema:
        return {"object"}
    return {"any"}


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _names(types: set[str]) -> str:
    return " or ".join(sorted(types))


def _short(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def graphql_diff(old_sdl: str, new_sdl: str) -> Report:
    """Breaking and dangerous changes between two GraphQL schemas (graphql-core)."""
    from graphql import build_schema, find_breaking_changes, find_dangerous_changes  # noqa: PLC0415

    old, new = build_schema(old_sdl), build_schema(new_sdl)
    return Report(
        [f"GraphQL: {change.description}" for change in find_breaking_changes(old, new)],
        [f"GraphQL: {change.description}" for change in find_dangerous_changes(old, new)],
    )


def _at(ref: str, path: str) -> str | None:
    result = subprocess.run(  # noqa: S603 - fixed command line
        ["git", "show", f"{ref}:{path}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def compare(base: str) -> Report:
    """Changes of the working tree's contract files against commit `base`."""
    report = Report()
    old_openapi = _at(base, OPENAPI)
    if old_openapi is None:
        report.dangerous.append(f"{OPENAPI} does not exist at {base}: REST API not compared")
    else:
        new_openapi = json.loads(Path(OPENAPI).read_text(encoding="utf-8"))
        rest = OpenApiDiff(json.loads(old_openapi), new_openapi).run()
        report.breaking += rest.breaking
        report.dangerous += rest.dangerous
    old_sdl = _at(base, GRAPHQL)
    if old_sdl is None:
        report.dangerous.append(f"{GRAPHQL} does not exist at {base}: GraphQL not compared")
    else:
        graphql = graphql_diff(old_sdl, Path(GRAPHQL).read_text(encoding="utf-8"))
        report.breaking += graphql.breaking
        report.dangerous += graphql.dangerous
    return report


def main() -> int:
    """Print the changes; exit 1 on a breaking change unless allowed."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="Commit to compare with, e.g. origin/main")
    parser.add_argument(
        "--allow-breaking", action="store_true", help="Report breaking changes without failing"
    )
    args = parser.parse_args()
    report = compare(args.base)
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    level = "warning" if args.allow_breaking else "error"
    for line in report.breaking:
        print(f"::{level} title=Breaking API change::{line}" if github else f"BREAKING  {line}")
    for line in report.dangerous:
        print(f"::notice title=API change to check::{line}" if github else f"CHECK     {line}")
    if not report.breaking:
        print(f"No breaking API change against {args.base}.")
        return 0
    if args.allow_breaking:
        print(f"{len(report.breaking)} breaking change(s), allowed (label breaking-change).")
        return 0
    print(
        f"{len(report.breaking)} breaking API change(s) against {args.base}. Keep the old "
        "contract, or make it a major release: label the pull request breaking-change."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
