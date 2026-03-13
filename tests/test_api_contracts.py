# pylint: disable=missing-function-docstring

from fastapi.testclient import TestClient

from app.main import app
from app.routes.v1 import telegram_routes
from app.telegram.exceptions import TelegramNotFoundError
from app.utils.config import settings

client = TestClient(app)


def test_healthz_response_contract() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_contains_feed_response_model() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi = response.json()

    feed_schema = openapi["paths"]["/v1/feed"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert feed_schema["$ref"].endswith("/FeedResponse")


def test_previews_accepts_object_payload(monkeypatch) -> None:
    async def _fake_previews(channels: list[str]) -> dict:
        return {channel: {"channel": {"title": "x", "subscribers": "1", "is_verified": False}} for channel in channels}

    monkeypatch.setattr(telegram_routes.telegram, "previews", _fake_previews)

    response = client.post("/v1/previews", json={"channels": ["telegram"]})
    if settings.ENABLE_PREVIEWS:
        assert response.status_code == 200
        assert "telegram" in response.json()
    else:
        assert response.status_code == 503


def test_previews_rejects_legacy_array_payload() -> None:
    response = client.post("/v1/previews", json=["telegram"])
    assert response.status_code == 422


def test_error_contract_maps_not_found(monkeypatch) -> None:
    async def _fake_preview(_channel: str) -> dict:
        raise TelegramNotFoundError("Requested Telegram resource was not found")

    monkeypatch.setattr(telegram_routes.telegram, "preview", _fake_preview)

    response = client.get("/v1/preview/durov")
    expected = 404 if settings.ENABLE_PREVIEWS else 503
    assert response.status_code == expected


def test_body_contract_maps_not_found(monkeypatch) -> None:
    async def _fake_body(_channel: str, _position: int = 0) -> dict:
        raise TelegramNotFoundError("Requested Telegram resource was not found")

    monkeypatch.setattr(telegram_routes.telegram, "body", _fake_body)

    response = client.get("/v1/body/durov")
    assert response.status_code == 404


def test_feed_feature_gate(monkeypatch) -> None:
    async def _fake_prepare(_self, _channels):
        return []

    monkeypatch.setattr(
        "app.routes.v1.feed.PostDataPreparer.prepare_multiple_channels",
        _fake_prepare,
    )

    response = client.post("/v1/feed", json={"channels": ["telegram"]})
    if settings.ENABLE_FEED:
        assert response.status_code == 200
    else:
        assert response.status_code == 503
