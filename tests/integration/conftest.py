"""Runs the built image as two servers with a real Valkey (compose.yaml).

Needs Docker and `TME_TEST_IMAGE`, the image to test; without it every test
here is skipped. These tests talk HTTP to the containers only, so they run
without the project installed (`make integration`, the CI `integration` job):

    uv run --only-group integration pytest --confcutdir tests/integration tests/integration

`TME_LOG_DIR` keeps the containers' logs there; `TME_KEEP_STACK=1` leaves the
stack running for a look afterwards (`docker compose -p telegram-me-it ...`).
"""

import hashlib
import itertools
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest

IMAGE = os.environ.get("TME_TEST_IMAGE", "")
# The release before, for the rolling-upgrade test (test_rolling_upgrade.py).
PREVIOUS_IMAGE = os.environ.get("TME_PREVIOUS_IMAGE", "")
COMPOSE_FILE = Path(__file__).with_name("compose.yaml")
UPSTREAM = "http://127.0.0.1:18080"
SERVERS = {
    "server-a": "http://127.0.0.1:18001",
    "server-b": "http://127.0.0.1:18002",
    "guarded-a": "http://127.0.0.1:18003",
    "guarded-b": "http://127.0.0.1:18004",
    "previous": "http://127.0.0.1:18005",
}


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """`docker compose` for the integration stack."""
    return subprocess.run(  # noqa: S603 - fixed command line
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],  # noqa: S607
        env={
            **os.environ,
            "APP_IMAGE": IMAGE,
            "PREVIOUS_IMAGE": PREVIOUS_IMAGE or IMAGE,
            "COMPOSE_PROFILES": "previous" if PREVIOUS_IMAGE else "",
        },
        check=check,
        capture_output=True,
        text=True,
    )


def unique(channel: str = "durov") -> str:
    """A channel nobody asked for yet, served with the pages of `channel`."""
    return f"{channel}_m{uuid.uuid4().int % 10**9}"


def client_ip() -> str:
    """A client address of its own, for the rate limit and proof of work."""
    value = uuid.uuid4().int
    return f"10.{value % 250}.{value // 250 % 250}.{value // 62500 % 250 + 1}"


def solve_pow(challenge: str, difficulty: int, method: str, target: str) -> str:
    """A nonce for a proof-of-work challenge (docs/api/protection.md)."""
    for n in itertools.count():
        nonce = format(n, "x")
        digest = hashlib.sha256(f"{challenge}\n{nonce}\n{method} {target}".encode()).digest()
        if int.from_bytes(digest, "big") >> (256 - difficulty) == 0:
            return nonce
    raise AssertionError


@dataclass
class Upstream:
    """The fake t.me: request counts and an artificial delay."""

    http: httpx2.Client

    def reset(self, delay: float = 0.0) -> None:
        """Forget the counts so far and set the delay of every page."""
        self.http.delete(f"{UPSTREAM}/__requests").raise_for_status()
        self.http.put(f"{UPSTREAM}/__delay", params={"seconds": delay}).raise_for_status()

    def counts(self) -> dict[str, Any]:
        """`{"total": n, "paths": {"GET /s/x": n, ...}}` since the last reset."""
        response = self.http.get(f"{UPSTREAM}/__requests")
        response.raise_for_status()
        counts: dict[str, Any] = response.json()
        return counts

    def fetches(self, path: str) -> int:
        """How often `path` (e.g. `GET /s/durov_m1`) was fetched since the last reset."""
        paths: dict[str, int] = self.counts()["paths"]
        fetched = ((name.partition(" ")[2], n) for name, n in paths.items())
        return sum(n for name, n in fetched if name == path or name.startswith(f"{path}/"))


@dataclass
class Stack:
    """The running stack."""

    http: httpx2.Client
    upstream: Upstream

    def url(self, server: str, path: str) -> str:
        """Absolute URL of `path` on one server."""
        return SERVERS[server] + path

    def get(self, server: str, path: str, ip: str | None = None, **kwargs: Any) -> httpx2.Response:
        """GET `path` from one server, as client `ip` when given."""
        headers = {**kwargs.pop("headers", {}), **({"x-forwarded-for": ip} if ip else {})}
        return self.http.get(self.url(server, path), headers=headers, **kwargs)

    def wait_until(self, condition: Any, timeout: float = 30.0, what: str = "condition") -> None:
        """Poll `condition()` until it is true."""
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}")
            time.sleep(0.5)


def _save_logs() -> None:
    log_dir = os.environ.get("TME_LOG_DIR")
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        logs = compose("logs", "--no-color", "--timestamps", check=False).stdout
        (Path(log_dir) / "integration.log").write_text(logs, encoding="utf-8")


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    if not IMAGE:
        pytest.skip("set TME_TEST_IMAGE to the image to test (see tests/integration/conftest.py)")
    started = compose("up", "-d", "--wait", "--wait-timeout", "120", check=False)
    if started.returncode != 0:
        _save_logs()
        logs = compose("logs", "--no-color", "--tail", "40", check=False).stdout
        compose("down", "-v", "--remove-orphans", check=False)
        pytest.fail(f"the integration stack did not start:\n{started.stderr}\n{logs}")
    with httpx2.Client(timeout=30) as http:
        try:
            yield Stack(http, Upstream(http))
        finally:
            _save_logs()
            if os.environ.get("TME_KEEP_STACK") != "1":
                compose("down", "-v", "--remove-orphans", check=False)


@pytest.fixture
def upstream(stack: Stack) -> Upstream:
    """The fake t.me, reset for this test."""
    stack.upstream.reset()
    return stack.upstream


def body_json(response: httpx2.Response) -> Any:
    """The parsed body of a successful response (fails the test otherwise)."""
    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
    return json.loads(response.content)
