"""Extraction of text entities (bold, links, custom emoji, ...) from message HTML.

Offsets refer to the plain text produced by stripping tags from the HTML,
mirroring how Telegram describes message entities.
"""

import re

from telegram_me.telegram.parsing.html import JSONDict

_PATTERNS = (
    r"(#\w+)",  # hashtag
    r"<b>(.+?)<\/b>(?![^<]*<\/i>)",  # bold
    r"<i>(.+?)<\/i>",  # italic
    r"<u>(.+?)<\/u>",  # underline
    r"<code>(.+?)<\/code>",  # code
    r"<s>(.+?)<\/s>",  # strikethrough
    r"<tg-spoiler>(.+?)<\/tg-spoiler>",  # spoiler
    # emoji
    r'<i\s+class="emoji"\s+style=".*?:url\(\'(.*?)\'\)">'
    r"<b>(.*?)<\/b><\/i>(?![^<]*<\/tg-emoji>)",
    # link in text with onclick
    r"<a\s+(?:[^>]*?\s+)?href=\"([^\"]*)\"[^>]*\s+onclick=\"[^\"]*\"[^>]*>(.*?)<\/a>",
    r"<a\s+(?:[^>]*?\s+)?href=\"(?!(?:.*#))(.*?)\"[^>]*>(.*?)<\/a>",  # url
    # animoji
    r'<tg-emoji.*?><i\s+class="emoji"\s+style="background-image:url\(\'(.*?)\'\)">'
    r"<b>(.*?)</b></i></tg-emoji>",
)
_COMBINED = re.compile("|".join(f"({pattern})" for pattern in _PATTERNS), re.DOTALL | re.MULTILINE)

# Indices in `match.groups()` that mark which alternative matched.
_TYPE_GROUPS = frozenset((1, 3, 5, 7, 9, 11, 13, 14, 17, 20, 23))
_ENTITY_TYPES: tuple[str | None, ...] = (
    "hashtag",
    "bold",
    "italic",
    "underline",
    "code",
    "strikethrough",
    "spoiler",
    "emoji",
    "text_link",
    None,
    "url",
    "animoji",
)
# Extra tag depth of the visible text for entities that carry a URL.
_URL_ENTITY_DEPTH = {"text_link": 0, "emoji": 1, "animoji": 2}
_LINE_BREAK = re.compile(r"<br\s?/?>")
_TAG = re.compile(r"<[^>]+>")


def _visible_text(match: re.Match[str], depth: int) -> str:
    return match.group().strip("<>").split(">")[-depth].split("<")[0]


def _classify(match: re.Match[str]) -> tuple[str | None, str | None, int]:
    for index, group in enumerate(match.groups()):
        if group and index in _TYPE_GROUPS:
            entity_type = _ENTITY_TYPES[index // 2] if index // 2 < len(_ENTITY_TYPES) else None
            if entity_type in _URL_ENTITY_DEPTH:
                return entity_type, match.group(index + 2), 1 + _URL_ENTITY_DEPTH[entity_type]
            return entity_type, None, 1
    return None, None, 1


def parse_entities(html: str) -> list[JSONDict]:
    """Return entities as dicts with `offset`, `length`, `type` and optional `url`."""
    html_text = _LINE_BREAK.sub("\n", html)
    text_only = _TAG.sub("", html_text)

    entities: list[JSONDict] = []
    offset = 0
    for match in _COMBINED.finditer(html_text):
        entity_type, url, depth = _classify(match)
        if not entity_type:
            continue
        visible = _visible_text(match, depth)
        start = text_only.find(visible, offset)
        entity: JSONDict = {"offset": start, "length": len(visible), "type": entity_type}
        if url:
            entity["url"] = f"https:{url}" if entity_type in ("emoji", "animoji") else url
        entities.append(entity)
        offset = start + len(visible)
    return entities
