"""Valkey goes away and comes back while both servers keep serving.

Runs last (file order): every server bypasses Redis for 30 s after the
outage, which would disturb the tests that count on sharing.
"""

import pytest

from tests.integration.conftest import Stack, Upstream, body_json, compose, unique

pytestmark = pytest.mark.integration


def test_valkey_outage_is_survived_and_sharing_resumes(stack: Stack, upstream: Upstream) -> None:
    cached = unique()
    body_json(stack.get("server-a", f"/v1/body/{cached}"))

    compose("stop", "valkey")
    try:
        # Served from memory or straight from t.me, never an error.
        assert stack.get("server-a", f"/v1/body/{cached}").status_code == 200
        for server in ("server-a", "server-b"):
            response = stack.get(server, f"/v1/body/{unique()}", headers={"connection": "close"})
            assert response.status_code == 200, response.text[:300]
    finally:
        compose("start", "valkey")
        compose("up", "-d", "--wait", "valkey")

    def shared_again() -> bool:
        channel = unique()
        body_json(stack.get("server-a", f"/v1/body/{channel}"))
        body_json(stack.get("server-b", f"/v1/body/{channel}"))
        return upstream.fetches(f"/s/{channel}") == 1

    # The circuit breaker bypasses Redis for 30 s after the outage.
    stack.wait_until(shared_again, timeout=60, what="servers sharing the cache again")
