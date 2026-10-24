"""During a rolling deployment the previous and the new release share Redis.

Server 1 already runs the new release while server 2 still runs the previous
one (`TME_PREVIOUS_IMAGE`, in CI the image production runs now), and after a
rollback it is the other way round. Each must read what the other cached: an
entry it cannot decode is either an error or a second fetch from t.me.

The previous release may not know `TELEGRAM_BASE_URL` and then fetches live
t.me, whose pages differ from the recorded ones (newer posts, other view
counts). Equal responses therefore show that the entry came from Redis.
"""

import pytest

from tests.integration.conftest import PREVIOUS_IMAGE, Stack, Upstream, body_json

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not PREVIOUS_IMAGE, reason="set TME_PREVIOUS_IMAGE to the release before"),
]


def test_previous_release_reads_what_the_new_one_cached(stack: Stack, upstream: Upstream) -> None:
    path = "/v1/body/stranaua"
    new = body_json(stack.get("server-a", path))
    old = body_json(stack.get("previous", path))
    assert old == new, (
        "the previous release answered differently from the entry the new one cached: "
        "it cannot read the new cache format (or renders it differently)"
    )
    assert upstream.fetches("/s/stranaua") == 1


def test_new_release_reads_what_the_previous_one_cached(stack: Stack, upstream: Upstream) -> None:
    path = "/v1/post/stranaua/252483"
    old = body_json(stack.get("previous", path))
    new = body_json(stack.get("server-b", path))
    assert new == old, (
        "the new release answered differently from the entry the previous one cached: "
        "it cannot read the old cache format (or renders it differently)"
    )
    assert upstream.fetches("/s/stranaua/252483") <= 1
