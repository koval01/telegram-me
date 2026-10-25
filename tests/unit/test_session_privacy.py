"""t.me's session never reaches a response or the cache.

t.me sets the cookie `stel_ssid` and also writes its value into its pages, in
the "Download Telegram" links (`telegram.org/dl?tme=<value>`). The cookie
stays in the native client's jar (`rust/telegram-me-native/tests/http_client.rs`);
these tests pin that the copy in the HTML is dropped by the parser, so that
neither what is cached (the parsed page) nor any response carries it.
"""

import gzip
import json
import re
from typing import Any

import pytest
from pydantic_core import to_json

from telegram_me.telegram.parsing.pages import (
    parse_channel_page,
    parse_more_page,
    parse_preview_page,
)
from tests.conftest import FIXTURES_DIR, MANIFEST, ClientFactory

_TOKEN = re.compile(r"dl\?tme=([0-9a-f]+_[0-9]+)")
_PAGES = [item for item in MANIFEST["responses"] if item["status"] == 200]


def _text(item: dict[str, Any]) -> str:
    return gzip.decompress((FIXTURES_DIR / item["file"]).read_bytes()).decode("utf-8")


_TOKENS = sorted({token for item in _PAGES for token in _TOKEN.findall(_text(item))})


def test_recorded_pages_carry_session_tokens() -> None:
    # Otherwise the tests below would prove nothing.
    assert len(_TOKENS) >= 2


@pytest.mark.parametrize("item", _PAGES, ids=lambda item: f"{item['method']} {item['path']}")
def test_parsed_pages_drop_the_session_token(item: dict[str, Any]) -> None:
    text = _text(item)
    data: dict[str, Any] | None
    if item["method"] == "POST":
        payload = json.loads(text)
        data = parse_more_page(payload["html"] if isinstance(payload, dict) else str(payload))
    elif item["path"].startswith("/s/"):
        data = parse_channel_page(text)
    else:
        data = parse_preview_page(text)
    # What the cache stores is encoded from this data.
    parsed = to_json(data).decode()
    assert "tme=" not in parsed
    assert not any(token in parsed for token in _TOKENS)


def test_responses_carry_no_session_token(make_client: ClientFactory) -> None:
    client = make_client()
    channels = sorted(MANIFEST["channels"])
    responses = [client.get(f"/v1/body/{channel}") for channel in channels]
    responses.append(client.post("/v1/previews", json={"channels": ["durov", "telegram"]}))
    responses.append(client.post("/v1/feed", json={"channels": channels}))
    for response in responses:
        assert response.status_code == 200, response.text
        assert "tme=" not in response.text
        assert not any(token in response.text for token in _TOKENS)
