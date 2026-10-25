"""scripts/smoke_matrix.py: invalid requests behind Cloudflare schema validation."""

import importlib.util
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import override

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    path = ROOT / "scripts" / "smoke_matrix.py"
    spec = importlib.util.spec_from_file_location("smoke_matrix", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


smoke = _load()

# Path -> (status, body) the stub edge answers with.
ANSWERS = {
    "/cloudflare": (403, b"error code: 1020"),
    # What Cloudflare sends a client that accepts JSON (trimmed).
    "/cloudflare-json": (
        403,
        b'{"title":"Error 1020: Access denied","status":403,"error_code":1020,'
        b'"error_name":"firewall_rule_blocked","cloudflare_error":true}',
    ),
    "/cloudflare-other": (403, b'{"status":403,"error_code":1010,"cloudflare_error":true}'),
    "/other": (403, b'{"detail":"Forbidden"}'),
    "/api": (422, b'{"detail":[]}'),
}


class Edge(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = ANSWERS[self.path]
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def edge() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Edge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def invalid(path: str) -> object:
    scenario = smoke.Scenario("invalid", "GET", path, {422})
    [relaxed] = smoke.edge_blocks_invalid([scenario])
    return relaxed


def test_only_validation_scenarios_accept_the_edge_block() -> None:
    scenarios = smoke.build_scenarios(True, True, [])
    relaxed = smoke.edge_blocks_invalid(scenarios)
    changed = [after for before, after in zip(scenarios, relaxed, strict=True) if before != after]
    assert {"body_invalid_short", "feed_invalid_missing"} <= {s.name for s in changed}
    assert all(s.expected_statuses == {422, 403} and s.edge_block for s in changed)
    assert len(changed) == sum(s.expected_statuses == {422} for s in scenarios)


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("/cloudflare", True),
        ("/cloudflare-json", True),
        ("/api", True),
        ("/cloudflare-other", False),
        ("/other", False),
    ],
)
def test_a_403_passes_only_as_a_cloudflare_block(edge: str, path: str, ok: bool) -> None:
    assert smoke.call(edge, invalid(path), 5).ok is ok


def test_without_the_flag_the_edge_block_fails(edge: str) -> None:
    scenario = smoke.Scenario("invalid", "GET", "/cloudflare", {422})
    assert not smoke.call(edge, scenario, 5).ok


def test_rollout_lets_the_edge_block_invalid_requests() -> None:
    rollout = (ROOT / "deploy" / "rollout.sh").read_text("utf-8")
    assert "--edge-blocks-invalid" in rollout
