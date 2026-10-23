"""Small helpers over lexbor nodes shared by the page parsers."""

import re
from functools import cache
from typing import Any
from urllib.parse import parse_qs, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

type JSONDict = dict[str, Any]

_BACKGROUND_URL = re.compile(
    r"background-image:\s*?url\([',\"](.*)[',\"]\)", re.IGNORECASE | re.MULTILINE
)


@cache
def _inner_html_pattern(tag: str) -> re.Pattern[str]:
    escaped = re.escape(tag)
    return re.compile(rf"<{escaped}.*?>(.*?)</{escaped}>", re.MULTILINE | re.DOTALL)


def inner_html(node: LexborNode | None, tag: str = "div") -> str | None:
    """Inner HTML of the first `<tag>` element in the node's serialized HTML."""
    if node is None or node.html is None:
        return None
    match = _inner_html_pattern(tag).search(node.html)
    return match.group(1) if match else None


def background_url(style: str | None) -> str | None:
    """URL from a `background-image:url('...')` CSS declaration."""
    if not style:
        return None
    match = _BACKGROUND_URL.search(style)
    return match.group(1) if match else None


def text_of(node: LexborNode | None) -> str | None:
    """Text content of a node, or None for a missing node."""
    return node.text() if node is not None else None


def first_query_param(url: str | None) -> dict[str, int | str]:
    """First query parameter of `url` as {name: value}, ints converted."""
    if not url:
        return {}
    for name, values in parse_qs(urlparse(url).query).items():
        value = values[0]
        return {name: int(value) if value.isdigit() else value}
    return {}


def meta_content(tree: LexborHTMLParser, attribute: str, value: str) -> str | None:
    """`content` of the first `<meta>` whose `attribute` equals `value`."""
    for meta in tree.css("meta"):
        if meta.attributes.get(attribute) == value:
            return meta.attributes.get("content")
    return None


def counters(nodes: list[LexborNode]) -> dict[str, str]:
    """Channel counters ({type: value}) from `.tgme_channel_info_counter` nodes."""
    result: dict[str, str] = {}
    for node in nodes:
        name = node.css_first(".counter_type")
        value = node.css_first(".counter_value")
        if name is not None and value is not None:
            result[name.text()] = value.text()
    return result


def header_labels(tree: LexborHTMLParser) -> list[str]:
    """Header labels such as `verified` from `.tgme_header_labels > i` classes."""
    labels = []
    for label in tree.css(".tgme_header_labels>i"):
        class_name = label.attributes.get("class")
        if class_name:
            labels.append(class_name.split("-")[0])
    return labels


def offsets(node: LexborNode | None, *, more: bool = False) -> dict[str, int | str]:
    """Pagination cursors from `<link rel=prev|next>` or "load more" anchors."""
    if node is None:
        return {}
    result: dict[str, int | str] = {}
    for link in node.css("a.tme_messages_more" if more else "link"):
        attributes = link.attributes
        is_page_link = attributes.get("rel") in ("prev", "next")
        is_more_link = "data-before" in attributes or "data-after" in attributes
        if is_page_link or is_more_link:
            result.update(first_query_param(attributes.get("href")))
    return result
