"""Two servers of the built image, two workers each, sharing a real Valkey.

What the unit tests can only show with fakeredis or inside one process: the
two-level cache, the cross-process fetch lease, the shared rate limit and
proof of work, over HTTP, between real granian workers and servers.
"""

import asyncio
import json
import time

import httpx2
import pytest

from tests.integration.conftest import (
    SERVERS,
    Stack,
    Upstream,
    body_json,
    client_ip,
    solve_pow,
    unique,
)

pytestmark = pytest.mark.integration

# (API path, the t.me page it needs) for a channel `{c}`.
ENDPOINTS = [
    ("/v1/body/{c}", "/s/{c}"),
    ("/v1/post/{c}/531", "/s/{c}/531"),
    ("/v1/more/{c}/before/531", "/s/{c}"),
    ("/v1/preview/{c}", "/{c}"),
]


@pytest.mark.parametrize(("path", "page"), ENDPOINTS, ids=["body", "post", "more", "preview"])
def test_second_server_reads_the_first_servers_cache(
    stack: Stack, upstream: Upstream, path: str, page: str
) -> None:
    channel = unique()
    first = body_json(stack.get("server-a", path.format(c=channel)))
    # New connections land on either worker of server B: none has it in memory.
    for _ in range(4):
        response = stack.get("server-b", path.format(c=channel), headers={"connection": "close"})
        assert body_json(response) == first
    assert upstream.fetches(page.format(c=channel)) == 1


async def test_concurrent_misses_on_both_servers_fetch_once(
    stack: Stack, upstream: Upstream
) -> None:
    """The fetch lease in Redis: 24 requests on 4 processes, one t.me request."""
    upstream.reset(delay=1.0)
    channel = unique()
    limits = httpx2.Limits(max_keepalive_connections=0)
    async with httpx2.AsyncClient(timeout=30, limits=limits) as http:
        responses = await asyncio.gather(
            *(
                http.get(f"{SERVERS[server]}/v1/body/{channel}")
                for server in ["server-a", "server-b"] * 12
            )
        )
    bodies = [body_json(response) for response in responses]
    assert all(body == bodies[0] for body in bodies)
    assert upstream.fetches(f"/s/{channel}") == 1


def test_shared_rate_limit_counts_a_client_on_every_server(stack: Stack) -> None:
    """RATE_LIMIT_RPM=20: no process alone sees 20 requests of this client.

    One connection per server, so one worker of each counts the client: 11
    requests each, 22 together. Each process pushes its counts to Redis once
    a second and blocks the client when the shared total is over the limit.
    """
    if time.time() % 60 > 45:
        # Counters are per minute; start in a fresh one.
        time.sleep(61 - time.time() % 60)
    ip = client_ip()
    servers = ["guarded-a", "guarded-b"]
    burst = [stack.get(server, "/healthz", ip=ip).status_code for server in servers * 11]
    assert burst == [200] * 22
    time.sleep(2.5)

    later: list[tuple[str, int]] = []
    for server in servers * 6:
        response = stack.get(server, "/healthz", ip=ip)
        later.append((server, response.status_code))
        if response.status_code == 429:
            assert int(response.headers["retry-after"]) >= 1
        time.sleep(0.25)
    # At most 17 requests per process, below its own burst of 20: only the
    # counters shared in Redis block the client, on both servers.
    assert [status for _, status in later[-6:]] == [429] * 6, later
    assert {server for server, status in later if status == 429} == set(servers)
    # Other clients are not affected.
    assert stack.get("guarded-a", "/healthz", ip=client_ip()).status_code == 200


def test_proof_of_work_from_one_server_is_accepted_by_the_other(stack: Stack) -> None:
    ip = client_ip()
    path = f"/v1/body/{unique()}"
    rejected = stack.get("guarded-a", path, ip=ip)
    assert rejected.status_code == 428
    pow_ = rejected.json()["pow"]
    assert pow_["code"] == "missing"
    proof = f"{pow_['challenge']}:{solve_pow(pow_['challenge'], pow_['difficulty'], 'GET', path)}"

    # The challenge is signed with the POW_SECRET every server shares.
    body_json(stack.get("guarded-b", path, ip=ip, headers={"x-pow": proof}))
    replayed = stack.get("guarded-b", path, ip=ip, headers={"x-pow": proof})
    assert (replayed.status_code, replayed.json()["pow"]["code"]) == (403, "replayed")
    forged = stack.get("guarded-b", path, ip=client_ip(), headers={"x-pow": proof})
    assert (forged.status_code, forged.json()["pow"]["code"]) == (403, "forged")

    challenge = stack.get("guarded-b", "/v1/pow/challenge", ip=ip)
    assert challenge.status_code == 200
    assert json.loads(challenge.content)["difficulty"] == 8


def test_the_image_serves_its_pages_and_documentation(stack: Stack) -> None:
    """The docs built into the image, under /docs/, with the PoW exemptions."""
    landing = stack.get("guarded-a", "/", ip=client_ip())
    assert landing.status_code == 200
    assert 'href="/docs/"' in landing.text
    assert stack.get("guarded-a", "/api", ip=client_ip()).status_code == 200

    for path in ("/docs/", "/docs/es/api/feed/", "/docs/ru/configuration/"):
        page = stack.get("guarded-a", path, ip=client_ip())
        assert page.status_code == 200, path
        assert f'<link rel="canonical" href="{path}">' in page.text
    asset = stack.get("guarded-a", "/docs/search.json", ip=client_ip())
    assert asset.status_code == 200

    sitemap = stack.get("guarded-a", "/sitemap.xml", ip=client_ip()).text
    for path in ("/docs/api/feed/", "/docs/es/api/feed/", "/docs/ru/api/feed/"):
        assert f"{path}</loc>" in sitemap
    assert 'hreflang="ru"' in sitemap
    robots = stack.get("guarded-a", "/robots.txt", ip=client_ip()).text
    assert "Disallow: /v1/" in robots
