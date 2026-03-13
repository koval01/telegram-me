import re
from urllib.parse import urlparse
from typing import Any, Optional

from selectolax.lexbor import LexborNode


def extract_emoji_from_style(emoji_node: LexborNode) -> Optional[str]:
    """Extract emoji sprite URL from style attribute."""
    style = emoji_node.attributes.get("style", "")
    if "background-image" not in style:
        return None

    match = re.search(r"url\(['\"]?(//[^'\")]+)['\"]?\)", style)
    if not match:
        return None

    url = "https:" + match.group(1)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def extract_single_reaction(reaction_node: LexborNode) -> Optional[dict[str, Any]]:
    """Parse one reaction node."""
    reaction = {"count": reaction_node.last_child.text()}

    if "tgme_reaction_paid" in reaction_node.attributes.get("class", ""):
        reaction.update({"type": "telegram_stars", "emoji": "⭐"})
        return reaction

    if reaction_node.css_first("i.emoji"):
        emoji_img = reaction_node.css_first("i.emoji")
        if not emoji_img:
            return None
        emoji_tag = emoji_img.css_first("b")
        if emoji_tag:
            reaction["emoji"] = emoji_tag.text()

        emoji = extract_emoji_from_style(emoji_img)
        if not emoji:
            return None

        reaction.update({"emoji_image": emoji, "type": "emoji"})
        return reaction

    custom_emoji_node = reaction_node.css_first("tg-emoji")
    if custom_emoji_node:
        reaction.update(
            {
                "type": "custom_emoji",
                "emoji_id": custom_emoji_node.attributes.get("emoji-id"),
            }
        )
        return reaction

    return None


def parse_reactions(message: LexborNode) -> Optional[list[dict[str, Any]]]:
    """Parse reactions list from message node."""
    reactions_node = message.css_first(".tgme_widget_message_reactions")
    if not reactions_node:
        return None

    reactions = []
    for reaction_node in reactions_node.css(".tgme_reaction"):
        reaction = extract_single_reaction(reaction_node)
        if reaction:
            reactions.append(reaction)

    return reactions or None
