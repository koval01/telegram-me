"""Parsers for whole t.me pages: channel feed, "load more" fragment, landing page."""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from telegram_me.telegram.errors import TelegramNotFoundError
from telegram_me.telegram.parsing.html import (
    JSONDict,
    counters,
    header_labels,
    inner_html,
    meta_content,
    offsets,
)
from telegram_me.telegram.parsing.post import parse_posts

_DESCRIPTION_DIV = re.compile(r"<div.*?>(.*?)</div>", re.DOTALL)
_LINK = re.compile(r"<a.*?>(.*?)</a>")


def parse_channel_page(html: str, *, simplified: bool = False) -> JSONDict:
    """Parse `t.me/s/<channel>` into channel info, posts and pagination offsets.

    Raises:
        TelegramNotFoundError: the page is not a public channel.
    """
    tree = LexborHTMLParser(html)
    username = tree.css_first(".tgme_channel_info_header_username>a")
    title = tree.css_first(".tgme_channel_info_header_title>span")
    if username is None or title is None:
        msg = "Requested Telegram resource was not found"
        raise TelegramNotFoundError(msg)

    description = meta_content(tree, "property", "og:description")
    channel: JSONDict = {
        "username": username.text()[1:],
        "title": {
            "string": meta_content(tree, "property", "og:title"),
            "html": inner_html(title, "span"),
        },
        "description": (
            {
                "string": description,
                "html": inner_html(tree.css_first(".tgme_channel_info_description")) or "",
            }
            if description
            else None
        ),
        "avatar": meta_content(tree, "property", "og:image"),
        "counters": counters(tree.css(".tgme_channel_info_counters>.tgme_channel_info_counter")),
        "labels": header_labels(tree),
    }
    # Pagination is read before posts: post parsing mutates the tree.
    meta = {"offset": offsets(tree.head)}
    return {
        "channel": channel,
        "content": {"posts": _posts(tree.body, simplified=simplified)},
        "meta": meta,
    }


def parse_more_page(html: str, *, simplified: bool = False) -> JSONDict:
    """Parse the HTML fragment returned by the "load more" endpoint."""
    tree = LexborHTMLParser(html)
    meta = {"offset": offsets(tree.body, more=True)}
    return {"posts": _posts(tree.body, simplified=simplified), "meta": meta}


def _posts(root: LexborNode | None, *, simplified: bool) -> list[JSONDict]:
    return parse_posts(root, simplified=simplified) if root is not None else []


def _preview_description(node: LexborNode | None) -> str | None:
    if node is None or node.html is None:
        return None
    match = _DESCRIPTION_DIV.match(node.html)
    if not match or not match.group(1):
        return None
    without_links = _LINK.sub(r"\g<1>", match.group(1))
    return without_links.replace("<br>", "\n") or None


def parse_preview_page(html: str) -> JSONDict | None:
    """Parse the `t.me/<channel>` landing page; None when it is not a channel."""
    page = LexborHTMLParser(html).css_first(".tgme_page")
    if page is None:
        return None
    title = page.css_first(".tgme_page_title>span")
    context_link = page.css_first(".tgme_page_context_link")
    if title is None or context_link is None or context_link.text() != "Preview channel":
        return None

    extra = page.css_first(".tgme_page_extra")
    channel: JSONDict = {
        "title": title.text(),
        "subscribers": extra.text() if extra else "",
        "is_verified": page.css_first("i.verified-icon") is not None,
    }
    if description := _preview_description(page.css_first(".tgme_page_description")):
        channel["description"] = description
    avatar = page.css_first("img.tgme_page_photo_image")
    if avatar is not None:
        channel["avatar"] = avatar.attributes.get("src")
    return {"channel": channel}
