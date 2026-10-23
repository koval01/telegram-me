"""Page parsing through the Python wrapper of the native (Rust) parser.

Low-level helpers (entities, durations, query strings, timestamps, the DOM
layer) are unit-tested in Rust (`cargo test`); full pages are pinned by the
contract snapshots. These tests cover the behaviour seen from Python.
"""

from typing import Any

import pytest

from telegram_me.telegram.errors import TelegramNotFoundError
from telegram_me.telegram.parsing.pages import (
    parse_channel_page,
    parse_more_page,
    parse_preview_page,
)
from telegram_me.validation import is_valid_channel, validate_channel_or_raise

_DATE = '<a class="tgme_widget_message_date"><time datetime="2026-10-20T10:00:00+00:00"></time></a>'
_BUTTONS = """
  <div class="tgme_widget_message_inline_keyboard">
    <div class="tgme_widget_message_inline_row">
      <a class="tgme_widget_message_inline_button" href="https://a.example"><span>A</span></a>
    </div>
    <div class="tgme_widget_message_inline_row">
      <a class="tgme_widget_message_inline_button" href="https://b.example"><span>B</span></a>
      <a class="tgme_widget_message_inline_button" href="tg://resolve?domain=c"><span>C</span></a>
    </div>
  </div>"""


def message(post_id: int, *, date: str = _DATE, bubble: str = "", extra: str = _BUTTONS) -> str:
    return f"""
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message"
     data-post="chan/{post_id}" data-view="v{post_id}">
  <div class="tgme_widget_message_bubble">
    {bubble}
    <div class="tgme_widget_message_text">Hello <b>world</b> #tag</div>
    <span class="tgme_widget_message_meta">{date}</span>
  </div>{extra}
</div></div>"""


def posts(*messages: str, simplified: bool = False) -> list[dict[str, Any]]:
    page = parse_more_page("".join(messages), simplified=simplified)
    result: list[dict[str, Any]] = page["posts"]
    return result


def test_post_text_entities_and_every_keyboard_row() -> None:
    [post] = posts(message(1))
    assert post["id"] == 1
    assert post["view"] == "v1"
    assert post["content"]["text"]["string"] == "Hello world #tag"
    assert post["content"]["text"]["html"] == "Hello <b>world</b> #tag"
    assert post["content"]["text"]["entities"] == [
        {"offset": 6, "length": 5, "type": "bold"},
        {"offset": 12, "length": 4, "type": "hashtag"},
    ]
    assert [b["title"] for b in post["content"]["inline"]] == ["A", "B", "C"]
    assert post["content"]["inline"][2]["url"] == "tg://resolve?domain=c"
    assert post["footer"] == {
        "views": None,
        "edited": False,
        "date": {"string": "2026-10-20T10:00:00+00:00", "unix": 1_792_490_400},
    }


def test_broken_post_is_skipped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    result = posts(message(1, date=""), message(2))
    assert [post["id"] for post in result] == [2]
    assert "Skipping unparsable post chan/1: ValueError: post has no publication time" in (
        caplog.text
    )


def test_post_without_id_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    broken = message(3).replace('data-post="chan/3"', 'data-post="chan"')
    assert posts(broken, message(4))[0]["id"] == 4
    assert "Skipping unparsable post chan: IndexError" in caplog.text


def test_simplified_mode_skips_entities_and_rich_parts() -> None:
    [post] = posts(message(1), simplified=True)
    assert "entities" not in post["content"]["text"]
    assert set(post["content"]) == {"text", "inline"}


def test_forwarded_name_and_author() -> None:
    forwarded = (
        '<div class="tgme_widget_message_forwarded_from">Forwarded from '
        '<a class="tgme_widget_message_forwarded_from_name" href="https://t.me/source">'
        '<span dir="auto">Source <b>Name</b></span></a>'
        ' (<span class="tgme_widget_message_forwarded_from_author" dir="auto">Bot</span>)'
        "</div>"
    )
    [post] = posts(message(1, bubble=forwarded))
    assert post["forwarded"] == {
        "name": {"string": "Source Name", "html": "Source <b>Name</b>"},
        "url": "https://t.me/source",
        "author": {"string": "Bot", "html": "Bot"},
    }


def test_media_kinds() -> None:
    media = """
      <div class="tgme_widget_message_sticker_wrap">
        <i class="tgme_widget_message_sticker" data-webp="https://cdn.example/s.webp"></i>
      </div>
      <a class="tgme_widget_message_voice_player">
        <audio class="tgme_widget_message_voice" src="https://cdn.example/v.ogg"
               data-waveform="abc"></audio>
        <time class="tgme_widget_message_voice_duration">0:07</time>
      </a>
      <div class="tgme_widget_message_video_player">
        <video class="tgme_widget_message_video"></video>
      </div>"""
    [post] = posts(message(1, bubble=media))
    assert post["content"]["media"] == [
        {"url": "https://cdn.example/s.webp", "type": "sticker"},
        {
            "url": "https://cdn.example/v.ogg",
            "waves": "abc",
            "duration": {"formatted": "0:07", "raw": 7},
            "type": "voice",
        },
        {"url": None, "thumb": None, "type": "gif", "available": False},
    ]


def test_more_page_offsets() -> None:
    fragment = (
        message(5) + '<a class="tme_messages_more" data-before="5" href="/s/chan?before=5"></a>'
    )
    assert parse_more_page(fragment)["meta"] == {"offset": {"before": 5}}


def test_not_a_channel_raises() -> None:
    with pytest.raises(TelegramNotFoundError):
        parse_channel_page("<html><body><p>nothing</p></body></html>")


def test_preview_page() -> None:
    page = """<div class="tgme_page">
      <div class="tgme_page_photo"><img class="tgme_page_photo_image" src="https://cdn/a.jpg"></div>
      <div class="tgme_page_title"><span dir="auto">Name</span><i class="verified-icon"></i></div>
      <div class="tgme_page_extra">1 234 subscribers</div>
      <div class="tgme_page_description">Line <a href="https://x">link</a><br>two</div>
      <a class="tgme_page_context_link">Preview channel</a>
    </div>"""
    assert parse_preview_page(page) == {
        "channel": {
            "title": "Name",
            "subscribers": "1 234 subscribers",
            "is_verified": True,
            "description": "Line link\ntwo",
            "avatar": "https://cdn/a.jpg",
        }
    }
    assert parse_preview_page("<div class='tgme_page'>user</div>") is None


def test_channel_validation() -> None:
    assert is_valid_channel("durov")
    assert is_valid_channel("Telegram_123")
    assert not is_valid_channel("_durov")
    assert not is_valid_channel("ab")
    assert validate_channel_or_raise(" durov ") == "durov"
    with pytest.raises(ValueError, match="Invalid Telegram username format"):
        validate_channel_or_raise("___")
