"""Rate limit and proof of work: middleware behaviour and the shared limit."""

import secrets
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from pydantic import ValidationError
from telegram_me_native import RateLimiter, solve_pow

from telegram_me.api.protection import sync_rate_limits
from telegram_me.cache import KEY_PREFIX, RedisCache
from tests.conftest import ClientFactory, make_settings

# Random per run: tests never embed a key.
SECRET = secrets.token_hex(32)


def pow_client(make_client: ClientFactory, **overrides: object) -> TestClient:
    settings: dict[str, object] = {"enable_pow": True, "pow_secret": SECRET, "pow_difficulty": 8}
    return make_client(**(settings | overrides))


def answer(challenge: dict[str, object], method: str, target: str) -> dict[str, str]:
    nonce = solve_pow(
        str(challenge["challenge"]), method, target, int(str(challenge["difficulty"]))
    )
    return {"X-PoW": f"{challenge['challenge']}:{nonce}"}


def challenge_of(client: TestClient) -> dict[str, object]:
    response = client.get("/v1/pow/challenge")
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


# Rate limit ---------------------------------------------------------------------------


def test_rate_limit_allows_a_burst_then_rejects(make_client: ClientFactory) -> None:
    client = make_client(enable_rate_limit=True, rate_limit_rpm=2)
    first = client.get("/healthz")
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert client.get("/healthz").status_code == 200
    blocked = client.get("/healthz")
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Rate limit exceeded"}
    # One request is replenished every 30 s.
    assert blocked.headers["retry-after"] == "30"
    assert blocked.headers["x-ratelimit-remaining"] == "0"


def test_rate_limit_burst_setting(make_client: ClientFactory) -> None:
    client = make_client(enable_rate_limit=True, rate_limit_rpm=60, rate_limit_burst=1)
    assert client.get("/healthz").status_code == 200
    blocked = client.get("/healthz")
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "1"


def test_rate_limit_skips_preflight_and_keeps_cors(make_client: ClientFactory) -> None:
    client = make_client(enable_rate_limit=True, rate_limit_rpm=1)
    client.get("/healthz")
    preflight = {"Origin": "https://example.com", "Access-Control-Request-Method": "GET"}
    assert client.options("/v1/body/durov", headers=preflight).status_code == 200
    blocked = client.get("/healthz", headers={"Origin": "https://example.com"})
    assert blocked.status_code == 429
    assert blocked.headers["access-control-allow-origin"] == "https://example.com"
    assert "Retry-After" in blocked.headers["access-control-expose-headers"]


def test_protection_is_off_by_default(client: TestClient) -> None:
    response = client.get("/healthz")
    assert "x-ratelimit-limit" not in response.headers
    assert client.get("/v1/pow/challenge").status_code == 404


@pytest.fixture
async def redis_cache() -> AsyncIterator[RedisCache]:
    cache = RedisCache(FakeAsyncRedis(decode_responses=True))
    yield cache
    await cache.close()


async def test_shared_limit_blocks_a_client_over_the_limit_everywhere(
    redis_cache: RedisCache,
) -> None:
    # Two processes, each allowing the client 3 requests within its own burst.
    first, second = RateLimiter(4, 4), RateLimiter(4, 4)
    for limiter in (first, second):
        for _ in range(3):
            assert limiter.check("203.0.113.7")[0]
    now = 1_800_000_010.0
    assert await sync_rate_limits(first, redis_cache, 4, now=now) == 0
    # 6 requests in this minute across both processes: over the limit of 4.
    assert await sync_rate_limits(second, redis_cache, 4, now=now) == 1
    allowed, _, retry_after = second.check("203.0.113.7")
    assert not allowed
    assert 49 < retry_after <= 50
    assert await redis_cache.client.get(f"{KEY_PREFIX}rl:30000000:203.0.113.7") == "6"
    assert second.check("198.51.100.1")[0]


async def test_shared_limit_without_traffic_or_redis_does_nothing(
    redis_cache: RedisCache,
) -> None:
    limiter = RateLimiter(1, 1)
    assert await sync_rate_limits(limiter, redis_cache, 1) == 0
    limiter.check("a")
    redis_cache.trip(OSError("down"))
    assert await sync_rate_limits(limiter, redis_cache, 1) == 0


# Proof of work ------------------------------------------------------------------------


def test_pow_needs_a_long_secret() -> None:
    with pytest.raises(ValidationError, match="POW_SECRET"):
        make_settings(enable_pow=True, pow_secret="short")  # noqa: S106
    with pytest.raises(ValidationError, match="POW_SECRET"):
        make_settings(enable_pow=True)


def test_challenge_endpoint(make_client: ClientFactory) -> None:
    challenge = challenge_of(pow_client(make_client))
    assert challenge["difficulty"] == 8
    assert challenge["algorithm"] == "sha256"
    assert challenge["header"] == "X-PoW"
    assert str(challenge["challenge"]).startswith("1.")


def test_missing_proof_returns_a_challenge(make_client: ClientFactory) -> None:
    response = pow_client(make_client).get("/v1/body/durov")
    assert response.status_code == 428
    body = response.json()
    assert body["detail"] == "Proof of work required"
    assert body["pow"]["code"] == "missing"
    assert response.headers["x-pow-challenge"] == body["pow"]["challenge"]
    assert response.headers["x-pow-difficulty"] == "8"
    assert response.headers["cache-control"] == "no-store"


def test_solved_request_passes_once(make_client: ClientFactory) -> None:
    client = pow_client(make_client)
    challenge = challenge_of(client)
    headers = answer(challenge, "GET", "/v1/body/durov")
    assert client.get("/v1/body/durov", headers=headers).status_code == 200
    replayed = client.get("/v1/body/durov", headers=headers)
    assert replayed.status_code == 403
    assert replayed.json()["pow"]["code"] == "replayed"


def test_challenge_is_reused_with_a_nonce_per_request(make_client: ClientFactory) -> None:
    client = pow_client(make_client)
    challenge = challenge_of(client)
    for target in ("/v1/body/durov", "/v1/body/nebudetgg", "/v1/preview/durov"):
        response = client.get(target, headers=answer(challenge, "GET", target))
        assert response.status_code != 428, target
        assert response.status_code != 403, target


def test_proof_is_bound_to_method_and_target(make_client: ClientFactory) -> None:
    client = pow_client(make_client, pow_difficulty=16)
    challenge = challenge_of(client)
    headers = answer(challenge, "GET", "/v1/body/durov")
    response = client.get("/v1/body/telegram", headers=headers)
    assert response.status_code == 403
    assert response.json()["pow"]["code"] == "insufficient_work"


def test_post_bodies_and_graphql_need_proof_too(make_client: ClientFactory) -> None:
    client = pow_client(make_client)
    assert client.post("/v1/previews", json={"channels": ["durov"]}).status_code == 428
    assert client.post("/graphql", json={"query": "{ __typename }"}).status_code == 428
    challenge = challenge_of(client)
    response = client.post(
        "/graphql", json={"query": "{ __typename }"}, headers=answer(challenge, "POST", "/graphql")
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("header", "status", "code"),
    [
        ("garbage", 400, "malformed"),
        ("1.1.8.AAAAAAAAAAAAAAAA.AAAAAAAAAAAAAAAAAAAAAA:0", 428, "expired"),
    ],
)
def test_bad_proofs(make_client: ClientFactory, header: str, status: int, code: str) -> None:
    response = pow_client(make_client).get("/v1/body/durov", headers={"X-PoW": header})
    assert response.status_code == status
    assert response.json()["pow"]["code"] == code


def test_forged_challenge_from_another_secret(make_client: ClientFactory) -> None:
    other = make_client(enable_pow=True, pow_secret=secrets.token_hex(32), pow_difficulty=8)
    challenge = challenge_of(other)
    response = pow_client(make_client).get(
        "/v1/body/durov", headers=answer(challenge, "GET", "/v1/body/durov")
    )
    assert response.status_code == 403
    assert response.json()["pow"]["code"] == "forged"


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/", 200),
        ("/api", 200),
        ("/openapi.json", 200),
        ("/robots.txt", 200),
        ("/sitemap.xml", 200),
        ("/static/landing.js", 200),
        ("/static/pow-worker.js", 200),
        ("/healthz", 200),
        ("/graphql", 200),
        # Redirects to the published docs here; served by the image.
        ("/docs", 302),
        ("/docs/es/api/", 302),
    ],
)
def test_pages_and_health_need_no_proof(make_client: ClientFactory, path: str, status: int) -> None:
    response = pow_client(make_client).get(path, follow_redirects=False)
    assert response.status_code == status


def test_landing_page_tells_the_browser_to_solve(make_client: ClientFactory) -> None:
    page = pow_client(make_client).get("/").text
    assert "&quot;pow&quot;:true" in page


def test_pages_are_rate_limited_like_the_api(make_client: ClientFactory) -> None:
    client = make_client(enable_rate_limit=True, rate_limit_rpm=1)
    assert client.get("/").status_code == 200
    assert client.get("/static/landing.css").status_code == 429


def test_rate_limit_runs_before_proof_of_work(make_client: ClientFactory) -> None:
    client = pow_client(make_client, enable_rate_limit=True, rate_limit_rpm=1)
    assert client.get("/v1/body/durov").status_code == 428
    assert client.get("/v1/body/durov").status_code == 429
