"""Reactions (emoji, custom emoji, Telegram Stars) attached to a post."""

import re

from selectolax.lexbor import LexborNode

from telegram_me.telegram.parsing.html import JSONDict

_EMOJI_URL = re.compile(r"url\(['\"]?(//[^'\")]+)['\"]?\)")


def _emoji_image(node: LexborNode) -> str | None:
    style = node.attributes.get("style") or ""
    if "background-image" not in style:
        return None
    match = _EMOJI_URL.search(style)
    if not match:
        return None
    # The pattern only matches protocol-relative URLs with a non-empty host.
    host = match.group(1)[2:].split("/", 1)[0]
    return f"https:{match.group(1)}" if host else None


def _reaction(node: LexborNode) -> JSONDict | None:
    count_node = node.last_child
    if count_node is None:
        return None
    reaction: JSONDict = {"count": count_node.text()}

    if "tgme_reaction_paid" in (node.attributes.get("class") or ""):
        reaction.update({"type": "telegram_stars", "emoji": "⭐"})
        return reaction

    emoji = node.css_first("i.emoji")
    if emoji is not None:
        symbol = emoji.css_first("b")
        if symbol is not None:
            reaction["emoji"] = symbol.text()
        image = _emoji_image(emoji)
        if not image:
            return None
        reaction.update({"emoji_image": image, "type": "emoji"})
        return reaction

    custom = node.css_first("tg-emoji")
    if custom is not None:
        reaction.update({"type": "custom_emoji", "emoji_id": custom.attributes.get("emoji-id")})
        return reaction
    return None


def parse_reactions(message: LexborNode) -> list[JSONDict] | None:
    """Reactions of a message, or None when it has none."""
    container = message.css_first(".tgme_widget_message_reactions")
    if container is None:
        return None
    reactions = [r for node in container.css(".tgme_reaction") if (r := _reaction(node))]
    return reactions or None
