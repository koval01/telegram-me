"""Parsing of channel posts (`.tgme_widget_message`) into JSON-ready dicts."""

import logging
import re
from datetime import datetime
from typing import Any

from selectolax.lexbor import LexborNode

from telegram_me.telegram.parsing.entities import parse_entities
from telegram_me.telegram.parsing.html import JSONDict, background_url, inner_html
from telegram_me.telegram.parsing.media import extract_media
from telegram_me.telegram.parsing.reactions import parse_reactions

logger = logging.getLogger(__name__)

_MESSAGE_SELECTOR = ".tgme_widget_message_wrap > .tgme_widget_message"
_FORMATTING_TAGS = ["a", "i", "b", "s", "u", "pre", "code", "span", "tg-emoji", "tg-spoiler"]
_LINE_BREAK = re.compile(r"<br\s?/?>")
_TEXT_DIV = re.compile(
    r'<div\s+class="tgme_widget_message_text(?:[^"]*)"(?:\s+[^>]*?)?>(.*?)(?:</div>|$)',
    re.DOTALL,
)
_REPLY_TARGET = re.compile(r"https://t\.me/[\w-]+/(\d+)")


def unix_timestamp(timestamp: str) -> int:
    """Seconds since the epoch for an ISO-8601 timestamp with offset."""
    return int(datetime.fromisoformat(timestamp).timestamp())


def _plain_text(html: str) -> str:
    text = _LINE_BREAK.sub("\n", html)
    match = _TEXT_DIV.search(text)
    text = match.group(1) if match else text
    return text.replace("&nbsp;", " ")


def _text(bubble: LexborNode, *, with_entities: bool) -> JSONDict | None:
    node = bubble.css_first(".tgme_widget_message_text, .js-message_text")
    if node is None:
        return None
    html = inner_html(node)
    node.unwrap_tags(_FORMATTING_TAGS)
    stripped = inner_html(node)
    if html is None or stripped is None:
        return None

    text: JSONDict = {"string": _plain_text(stripped), "html": html}
    if with_entities and (entities := parse_entities(html)):
        text["entities"] = entities
    return text


def _inline_buttons(message: LexborNode) -> list[JSONDict] | None:
    """Buttons of the inline keyboard, row by row, flattened into one list."""
    keyboard = message.css_first(".tgme_widget_message_inline_keyboard")
    if keyboard is None:
        return None
    buttons = []
    for button in keyboard.css(
        ".tgme_widget_message_inline_row .tgme_widget_message_inline_button"
    ):
        title = button.css_first("span")
        buttons.append(
            {"title": title.text() if title else "", "url": button.attributes.get("href")}
        )
    return buttons


def _poll(bubble: LexborNode) -> JSONDict | None:
    poll = bubble.css_first(".tgme_widget_message_poll")
    if poll is None:
        return None

    options = []
    for option in poll.css(".tgme_widget_message_poll_option"):
        name = option.css_first(".tgme_widget_message_poll_option_text")
        percent = option.css_first(".tgme_widget_message_poll_option_percent")
        if name is None or percent is None:
            continue
        value = percent.text().removesuffix("%")
        if value.isdigit():
            options.append({"name": name.text(), "percent": int(value)})

    question = poll.css_first(".tgme_widget_message_poll_question")
    poll_type = poll.css_first(".tgme_widget_message_poll_type")
    voters = bubble.css_first(".tgme_widget_message_voters")
    return {
        "question": question.text() if question else None,
        "type": poll_type.text() if poll_type else None,
        "votes": voters.text() if voters else None,
        "options": options,
    }


def _preview_link(message: LexborNode) -> JSONDict | None:
    preview = message.css_first(".tgme_widget_message_link_preview")
    if preview is None:
        return None
    site_name = preview.css_first(".link_preview_site_name")
    title = preview.css_first(".link_preview_title")
    description = preview.css_first(".link_preview_description")
    thumb = preview.css_first(".tgme_widget_message_link_preview > .link_preview_right_image")
    return {
        "site_name": site_name.text(strip=True) if site_name else None,
        "url": preview.attributes.get("href"),
        "title": title.text(strip=True) if title else None,
        "description": (
            {"string": description.text(), "html": inner_html(description)} if description else None
        ),
        "thumb": background_url(thumb.attributes.get("style")) if thumb else None,
    }


def _reply(message: LexborNode) -> JSONDict | None:
    reply = message.css_first(".tgme_widget_message_reply")
    if reply is None:
        return None
    text = reply.css_first(".tgme_widget_message_metatext, .tgme_widget_message_text")
    name = reply.css_first(".tgme_widget_message_author")
    target = _REPLY_TARGET.search(reply.attributes.get("href") or "")
    if text is None or name is None or target is None:
        return None
    cover = reply.css_first(".tgme_widget_message_reply_thumb")
    return {
        "cover": background_url(cover.attributes.get("style")) if cover else None,
        "name": {"string": name.text().strip(), "html": inner_html(name, "span")},
        "text": {"string": text.text(), "html": inner_html(text)},
        "to_message": int(target.group(1)),
    }


def _name(node: LexborNode | None) -> JSONDict | None:
    if node is None:
        return None
    return {"string": node.text(), "html": inner_html(node, "span")}


def _footer(bubble: LexborNode) -> JSONDict:
    time = bubble.css_first(".tgme_widget_message_date > time")
    timestamp = time.attributes.get("datetime") if time is not None else None
    if not timestamp:
        msg = "post has no publication time"
        raise ValueError(msg)
    views = bubble.css_first(".tgme_widget_message_views")
    meta = bubble.css_first(".tgme_widget_message_meta")

    footer: JSONDict = {
        "views": views.text() if views else None,
        "edited": meta is not None and meta.text().startswith("edited"),
        "date": {"string": timestamp, "unix": unix_timestamp(timestamp)},
    }
    if author := _name(bubble.css_first(".tgme_widget_message_from_author")):
        footer["author"] = author
    return footer


def _forwarded(message: LexborNode) -> JSONDict | None:
    node = message.css_first(".tgme_widget_message_forwarded_from_name")
    if node is None:
        return None
    forwarded: JSONDict = {"name": _name(node)}
    if url := node.attributes.get("href"):
        forwarded["url"] = url
    return forwarded


def post_id(message: LexborNode) -> int:
    """Numeric post id from `data-post="<channel>/<id>"`."""
    data_post = message.attributes.get("data-post") or ""
    return int(data_post.split("/")[1])


def _parse_message(message: LexborNode, identifier: int, *, simplified: bool) -> JSONDict:
    bubble = message.css_first(".tgme_widget_message_bubble")
    if bubble is None:
        msg = "post has no message bubble"
        raise ValueError(msg)

    content: dict[str, Any]
    if simplified:
        content = {
            "text": _text(bubble, with_entities=False),
            "media": extract_media(bubble),
            "inline": _inline_buttons(message),
        }
    else:
        content = {
            "text": _text(bubble, with_entities=True),
            "media": extract_media(bubble),
            "poll": _poll(bubble),
            "inline": _inline_buttons(message),
            "reply": _reply(message),
            "preview_link": _preview_link(message),
            "reacts": parse_reactions(message),
        }

    post: JSONDict = {
        "id": identifier,
        "content": {key: value for key, value in content.items() if value},
        "footer": _footer(bubble),
        "view": message.attributes.get("data-view"),
    }
    if forwarded := _forwarded(message):
        post["forwarded"] = forwarded
    return post


def parse_posts(root: LexborNode, *, simplified: bool = False) -> list[JSONDict]:
    """Parse every post under `root`, skipping (and logging) malformed ones.

    The node is modified in place (formatting tags inside message texts are
    unwrapped), so it must not be reused for other extraction afterwards.
    """
    posts = []
    for message in root.css(_MESSAGE_SELECTOR):
        try:
            identifier = post_id(message)
            posts.append(_parse_message(message, identifier, simplified=simplified))
        except Exception as exc:
            data_post = message.attributes.get("data-post")
            logger.warning(
                "Skipping unparsable post %s: %r", data_post, exc, extra={"data_post": data_post}
            )
    return posts
