"""Two servers of the built image, two workers each, joined by the peer layer.

No Redis: the servers find each other by their compose names (`PEERS`) and
share their work over the peer protocol, from container to container.
"""

import asyncio

import httpx2
import pytest

from tests.integration.conftest import SERVERS, Stack, Upstream, body_json, compose, unique

pytestmark = pytest.mark.integration

PEERS = ["peer-a", "peer-b"]


def joined(stack: Stack, upstream: Upstream) -> bool:
    """One fresh channel, asked of every worker of both servers, fetched once."""
    channel = unique()
    for server in PEERS * 4:
        # New connections land on either worker of a server.
        response = stack.get(server, f"/v1/body/{channel}", headers={"connection": "close"})
        body_json(response)
    return upstream.fetches(f"/s/{channel}") == 1


@pytest.fixture
def peers(stack: Stack, upstream: Upstream) -> Upstream:
    """The peer servers, once all four workers know each other."""
    stack.wait_until(lambda: joined(stack, upstream), timeout=60, what="the peers to join")
    upstream.reset()
    return upstream


def test_every_worker_reads_what_one_fetched(stack: Stack, peers: Upstream) -> None:
    for path, page in [
        ("/v1/body/{c}", "/s/{c}"),
        ("/v1/post/{c}/531", "/s/{c}/531"),
        ("/v1/more/{c}/before/531", "/s/{c}"),
        ("/v1/preview/{c}", "/{c}"),
    ]:
        channel = unique()
        answers = {
            stack.get(server, path.format(c=channel), headers={"connection": "close"}).content
            for server in PEERS * 4
        }
        assert len(answers) == 1, path
        assert peers.fetches(page.format(c=channel)) == 1, path


async def test_concurrent_misses_on_both_servers_fetch_once(stack: Stack, peers: Upstream) -> None:
    peers.reset(delay=1.0)
    channel = unique()
    limits = httpx2.Limits(max_keepalive_connections=0)
    async with httpx2.AsyncClient(timeout=30, limits=limits) as http:
        responses = await asyncio.gather(
            *(http.get(f"{SERVERS[server]}/v1/body/{channel}") for server in PEERS * 12)
        )
    assert {response.status_code for response in responses} == {200}
    assert len({response.content for response in responses}) == 1
    assert peers.fetches(f"/s/{channel}") == 1


def test_a_server_that_goes_down_is_not_waited_for(stack: Stack, peers: Upstream) -> None:
    compose("kill", "peer-b")
    try:
        # Channels peer-b owned are fetched by peer-a now, without waiting.
        for _ in range(10):
            response = stack.get("peer-a", f"/v1/body/{unique()}", timeout=5)
            assert response.status_code == 200
            assert response.elapsed.total_seconds() < 2
    finally:
        compose("up", "-d", "--wait", "peer-b")
    stack.wait_until(lambda: joined(stack, peers), timeout=60, what="peer-b to rejoin")
