from datetime import UTC, datetime

import pytest
from selectolax.lexbor import LexborHTMLParser

from telegram_me.services.scoring import (
    ScoredPost,
    content_summary,
    parse_compact_number,
    parse_published_at,
    reaction_counts,
)
from telegram_me.telegram.parsing.entities import parse_entities
from telegram_me.telegram.parsing.html import first_query_param
from telegram_me.telegram.parsing.media import duration_seconds
from telegram_me.telegram.parsing.post import parse_posts, unix_timestamp
from telegram_me.validation import is_valid_channel, validate_channel_or_raise

_MESSAGE = """
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="chan/{id}"
     data-view="v{id}">
  <div class="tgme_widget_message_bubble">
    <div class="tgme_widget_message_text">Hello <b>world</b></div>
    <span class="tgme_widget_message_meta">{date}</span>
  </div>
  <div class="tgme_widget_message_inline_keyboard">
    <div class="tgme_widget_message_inline_row">
      <a class="tgme_widget_message_inline_button" href="https://a.example"><span>A</span></a>
    </div>
    <div class="tgme_widget_message_inline_row">
      <a class="tgme_widget_message_inline_button" href="https://b.example"><span>B</span></a>
      <a class="tgme_widget_message_inline_button" href="https://c.example"><span>C</span></a>
    </div>
  </div>
</div></div>
"""
_DATE = '<a class="tgme_widget_message_date"><time datetime="2026-10-20T10:00:00+00:00"></time></a>'


def _page(*messages: str) -> LexborHTMLParser:
    return LexborHTMLParser(f"<html><body>{''.join(messages)}</body></html>")


def test_parse_posts_reads_text_entities_and_every_keyboard_row() -> None:
    tree = _page(_MESSAGE.format(id=1, date=_DATE))
    [post] = parse_posts(tree.body)  # type: ignore[arg-type]

    assert post["id"] == 1
    assert post["view"] == "v1"
    assert post["content"]["text"]["string"] == "Hello world"
    assert post["content"]["text"]["entities"] == [{"offset": 6, "length": 5, "type": "bold"}]
    assert [button["title"] for button in post["content"]["inline"]] == ["A", "B", "C"]
    assert post["footer"]["date"] == {
        "string": "2026-10-20T10:00:00+00:00",
        "unix": 1_792_490_400,
    }


def test_broken_post_is_skipped_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    tree = _page(_MESSAGE.format(id=1, date=""), _MESSAGE.format(id=2, date=_DATE))
    posts = parse_posts(tree.body)  # type: ignore[arg-type]
    assert [post["id"] for post in posts] == [2]
    assert "Skipping unparsable post chan/1" in caplog.text


def test_simplified_mode_skips_entities() -> None:
    tree = _page(_MESSAGE.format(id=1, date=_DATE))
    [post] = parse_posts(tree.body, simplified=True)  # type: ignore[arg-type]
    assert "entities" not in post["content"]["text"]


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("#tag text", [{"offset": 0, "length": 4, "type": "hashtag"}]),
        ("a <i>b</i>", [{"offset": 2, "length": 1, "type": "italic"}]),
        ("<code>x()</code>", [{"offset": 0, "length": 3, "type": "code"}]),
        (
            '<a href="https://example.com" onclick="return false;">site</a>',
            [{"offset": 0, "length": 4, "type": "text_link", "url": "https://example.com"}],
        ),
        ("plain", []),
    ],
)
def test_entities(html: str, expected: list[dict[str, object]]) -> None:
    assert parse_entities(html) == expected


@pytest.mark.parametrize(
    ("value", "seconds"), [("0:05", 5), ("2:30", 150), ("1:02:03", 3723), ("x", None), ("", None)]
)
def test_duration_seconds(value: str, seconds: int | None) -> None:
    assert duration_seconds(value) == seconds


def test_first_query_param() -> None:
    assert first_query_param("/s/durov?before=42") == {"before": 42}
    assert first_query_param("/s/durov?q=abc") == {"q": "abc"}
    assert first_query_param(None) == {}


def test_unix_timestamp_accepts_both_offset_styles() -> None:
    assert unix_timestamp("2026-10-20T10:00:00+00:00") == unix_timestamp("2026-10-20T10:00:00+0000")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("15", 15), ("1.2K", 1200), ("3.5M", 3_500_000), ("15 400", 15400), ("n/a", 0), (None, 0)],
)
def test_parse_compact_number(value: str | None, expected: int) -> None:
    assert parse_compact_number(value) == expected


def test_scoring_prefers_fresh_engaging_posts() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)

    def score(published: str, views: int) -> float:
        return ScoredPost(
            published_at=parse_published_at(published),
            views=views,
            content=content_summary({"text": {"string": "x" * 100}, "media": [{"type": "image"}]}),
            reactions=reaction_counts([{"emoji": "👍", "count": "10"}]),
            subscribers=1000,
        ).score(now)

    fresh = score("2026-10-22T23:00:00+00:00", 500)
    old = score("2026-10-01T00:00:00+00:00", 500)
    quiet = score("2026-10-22T23:00:00+00:00", 5)
    assert fresh > quiet > old
    assert 0 <= old < fresh <= 10


def test_channel_validation() -> None:
    assert is_valid_channel("durov")
    assert is_valid_channel("Telegram_123")
    assert not is_valid_channel("_durov")
    assert not is_valid_channel("ab")
    assert validate_channel_or_raise(" durov ") == "durov"
    with pytest.raises(ValueError, match="Invalid Telegram username format"):
        validate_channel_or_raise("___")
