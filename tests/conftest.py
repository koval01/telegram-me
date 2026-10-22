import gzip
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures" / "telegram"
SNAPSHOTS_DIR = TESTS_DIR / "snapshots"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def _normalize_path(path: str) -> str:
    """Treat `/s/<channel>`, `/s/<channel>/0` and `/s/<channel>/None` as one page."""
    for suffix in ("/None", "/0"):
        if path.startswith("/s/") and path.endswith(suffix):
            return path[: -len(suffix)]
    return path


_RESPONSES = {
    (item["method"], _normalize_path(item["path"]), tuple(sorted(item["params"].items()))): item
    for item in MANIFEST["responses"]
}


def _telegram_side_effect(request: httpx.Request) -> httpx.Response:
    params = dict(request.url.params)
    if "embed" in params:
        # Comment counters are not part of the recorded fixtures.
        return httpx.Response(404, text="")

    key = (request.method, _normalize_path(request.url.path), tuple(sorted(params.items())))
    item = _RESPONSES.get(key)
    if item is None:
        raise AssertionError(f"Unexpected upstream request in tests: {request.method} {request.url}")

    body = gzip.decompress((FIXTURES_DIR / item["file"]).read_bytes()).decode("utf-8")
    return httpx.Response(
        item["status"], text=body, headers={"content-type": item["content_type"]}
    )


@pytest.fixture
def telegram_fixtures() -> Iterator[dict[str, Any]]:
    """Serve recorded t.me responses; any unrecorded request fails the test."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.route(host="t.me").mock(side_effect=_telegram_side_effect)
        yield MANIFEST


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> Callable[[str, Any], None]:
    """Compare data with tests/snapshots/<name>.json (UPDATE_SNAPSHOTS=1 rewrites)."""
    update = os.environ.get("UPDATE_SNAPSHOTS") == "1"

    def check(name: str, data: Any) -> None:
        path = SNAPSHOTS_DIR / f"{name}.json"
        serialized = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(serialized, encoding="utf-8")
            return
        if not path.exists():
            pytest.fail(f"Missing snapshot {path.name}; run with UPDATE_SNAPSHOTS=1")
        assert json.loads(path.read_text(encoding="utf-8")) == json.loads(serialized), (
            f"Snapshot {path.name} differs (request: {request.node.name})"
        )

    return check
