import re
from datetime import datetime
from typing import Any, Optional

from selectolax.lexbor import LexborNode

from app.telegram.parser.methods.utils import Utils


def unix_timestamp(timestamp: str) -> int:
    """Convert timestamp string to unix seconds."""
    parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
    return int(parsed.timestamp())


def clean_html_text(html_content: str) -> str:
    """Remove wrapping tags and normalize textual content."""
    text = re.sub(r"<br\s?/?>", "\n", html_content)
    div = re.compile(
        r'<div\s+class="tgme_widget_message_text(?:[^"]*)"(?:\s+[^>]*?)?>(.*?)(?:</div>|$)',
        flags=re.DOTALL,
    )
    div_match = re.search(div, text)
    text = div_match.group(1) if div_match else text
    return text.replace("&nbsp;", " ")


def extract_preview_link(bubble: LexborNode) -> Optional[dict[str, Any]]:
    """Extract preview link payload."""
    preview = bubble.css_first(".tgme_widget_message_link_preview")
    if not preview:
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
            {"string": description.text(), "html": Utils.get_text_html(description)}
            if description
            else None
        ),
        "thumb": (
            Utils.background_extr(thumb.attributes.get("style"))
            if thumb and thumb.attributes.get("style")
            else None
        ),
    }


def extract_poll(bubble: LexborNode) -> Optional[dict[str, Any]]:
    """Extract poll payload."""
    poll = bubble.css_first(".tgme_widget_message_poll")
    if not poll:
        return None

    question = poll.css_first(".tgme_widget_message_poll_question")
    poll_type = poll.css_first(".tgme_widget_message_poll_type")
    options = poll.css(".tgme_widget_message_poll_option")

    parsed_options = []
    for option in options:
        option_text = option.css_first(".tgme_widget_message_poll_option_text")
        option_percent = option.css_first(".tgme_widget_message_poll_option_percent")
        if option_text and option_percent:
            parsed_options.append(
                {
                    "name": option_text.text(),
                    "percent": int(option_percent.text()[:-1]),
                }
            )

    return {
        "question": question.text() if question else None,
        "type": poll_type.text() if poll_type else None,
        "votes": bubble.css_first(".tgme_widget_message_voters").text(),
        "options": parsed_options,
    }


def extract_reply_data(reply: LexborNode) -> tuple[Optional[LexborNode], Optional[LexborNode], Optional[LexborNode]]:
    """Extract basic reply nodes."""
    cover = reply.css_first(".tgme_widget_message_reply_thumb")
    name = reply.css_first(".tgme_widget_message_author")
    text = reply.css_first(".tgme_widget_message_metatext, .tgme_widget_message_text")
    return cover, name, text
