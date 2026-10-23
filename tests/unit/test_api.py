from typing import Any

import pytest
from fastapi.testclient import TestClient

from telegram_me.app import create_app
from tests.conftest import ClientFactory, FixtureFetcher, make_settings


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_static_files_are_served(client: TestClient) -> None:
    response = client.get("/static/hello.txt")
    assert response.status_code == 200
    assert response.text.startswith("Hello static!")


def test_docs_and_openapi(client: TestClient) -> None:
    assert "Swagger" in client.get("/").text
    spec = client.get("/openapi.json").json()
    assert set(spec["paths"]) >= {
        "/healthz",
        "/v1/body/{channel}",
        "/v1/more/{channel}/{direction}/{position}",
        "/v1/post/{channel}/{identifier}",
        "/v1/preview/{channel}",
        "/v1/previews",
        "/v1/feed",
    }
    assert "503" in spec["paths"]["/v1/preview/{channel}"]["get"]["responses"]
    assert "503" in spec["paths"]["/v1/feed"]["post"]["responses"]
    feed_schema = spec["paths"]["/v1/feed"]["post"]["responses"]["200"]["content"]
    assert feed_schema["application/json"]["schema"]["$ref"].endswith("/FeedResponse")
    assert spec["info"]["version"].startswith("2.")


def test_docs_can_be_disabled(make_client: ClientFactory) -> None:
    client = make_client(disable_docs=True)
    assert client.get("/").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_feed_is_disabled_by_default(fetcher: FixtureFetcher) -> None:
    settings = make_settings()
    defaults = type(settings)(_env_file=None)
    assert defaults.enable_feed is False
    with TestClient(create_app(defaults, fetcher=fetcher)) as client:
        response = client.post("/v1/feed", json={"channels": ["durov"]})
    assert response.status_code == 503
    assert response.json() == {"detail": "Feature 'feed' is disabled by server configuration"}


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/v1/preview/bad-name", None),
        ("POST", "/v1/previews", ["durov"]),
        ("POST", "/v1/previews", {"channels": []}),
        ("POST", "/v1/feed", {"channels": ["bad-name"]}),
        ("POST", "/v1/feed", {"not_channels": ["durov"]}),
    ],
)
def test_validation_runs_before_feature_gate(
    make_client: ClientFactory, method: str, path: str, payload: Any
) -> None:
    # 1.x contract: invalid input is 422 even while the feature is disabled.
    client = make_client(enable_feed=False, enable_previews=False)
    assert client.request(method, path, json=payload).status_code == 422


def test_previews_can_be_disabled(make_client: ClientFactory) -> None:
    client = make_client(enable_previews=False)
    assert client.get("/v1/preview/durov").status_code == 503
    assert client.post("/v1/previews", json={"channels": ["durov"]}).status_code == 503


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/v1/body/ab", None),
        ("GET", "/v1/body/durov-chan", None),
        ("GET", "/v1/body/durov?position=0", None),
        ("GET", "/v1/more/durov/sideways/10", None),
        ("GET", "/v1/more/durov/after/0", None),
        ("GET", "/v1/post/durov/notint", None),
        ("GET", "/v1/post/durov/10000001", None),
        ("GET", "/v1/preview/bad-name", None),
        ("POST", "/v1/previews", ["durov"]),
        ("POST", "/v1/previews", {"channels": []}),
        ("POST", "/v1/previews", {"channels": ["durov", "bad-name"]}),
        ("POST", "/v1/previews", {"channels": [f"chan{i:03d}" for i in range(101)]}),
        ("POST", "/v1/feed", {"channels": []}),
        ("POST", "/v1/feed", {"not_channels": ["durov"]}),
    ],
)
def test_invalid_input_is_rejected(
    client: TestClient, fetcher: FixtureFetcher, method: str, path: str, payload: Any
) -> None:
    response = client.request(method, path, json=payload)
    assert response.status_code == 422
    assert fetcher.requests == []


def test_response_headers(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers["x-app-node"]
    assert response.headers["x-process-time"].endswith(" ms")
    assert "cache-control" not in response.headers


@pytest.mark.parametrize(
    ("cache_mode", "expected"),
    [
        ("normal", "public, max-age=10, stale-while-revalidate=10"),
        ("aggressive", "public, max-age=120, stale-while-revalidate=240"),
        ("off", None),
    ],
)
def test_cloudflare_cache_control(
    make_client: ClientFactory, cache_mode: str, expected: str | None
) -> None:
    client = make_client(cache_mode=cache_mode)
    response = client.get("/healthz", headers={"CF-RAY": "abc"})
    assert response.headers.get("cache-control") == expected


def test_cors_allows_any_origin_by_default(client: TestClient) -> None:
    response = client.options(
        "/v1/body/durov",
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://example.com"


def test_rate_limit(make_client: ClientFactory) -> None:
    client = make_client(enable_rate_limit=True, rate_limit_rpm=2)
    first = client.get("/healthz")
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert client.get("/healthz").status_code == 200
    blocked = client.get("/healthz")
    assert blocked.status_code == 429
    assert blocked.text == "Rate limit exceeded"
    assert blocked.headers["retry-after"] == "60"


def test_upstream_errors_map_to_503(make_client: ClientFactory, fetcher: FixtureFetcher) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> Any:
        from telegram_me.telegram.errors import TelegramUpstreamError  # noqa: PLC0415

        msg = "Telegram upstream returned status 500"
        raise TelegramUpstreamError(msg)

    fetcher.fetch = unavailable  # type: ignore[method-assign]
    client = make_client()
    response = client.get("/v1/body/durov")
    assert response.status_code == 503
    assert response.json() == {"detail": "Telegram upstream returned status 500"}
    # Every channel failing for a non-404 reason surfaces the error too.
    assert client.post("/v1/previews", json={"channels": ["durov"]}).status_code == 503
