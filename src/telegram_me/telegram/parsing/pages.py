"""Parsers for t.me pages: channel feed, "load more" fragment, landing page.

Parsing runs natively in Rust (`telegram_me_native`, html5ever) with the GIL
released; this module only adapts the results: JSON is decoded by
pydantic-core and skipped posts are logged.
"""

import logging
from typing import Any

import telegram_me_native as native
from pydantic_core import from_json

from telegram_me.telegram.errors import TelegramNotFoundError

type JSONDict = dict[str, Any]

logger = logging.getLogger(__name__)


def _decode(page: str, skipped: list[tuple[str, str]]) -> JSONDict:
    for data_post, reason in skipped:
        logger.warning(
            "Skipping unparsable post %s: %s", data_post, reason, extra={"data_post": data_post}
        )
    data: JSONDict = from_json(page)
    return data


def parse_channel_page(html: str, *, simplified: bool = False) -> JSONDict:
    """Parse `t.me/s/<channel>` into channel info, posts and pagination offsets.

    Raises:
        TelegramNotFoundError: the page is not a public channel.
    """
    result = native.parse_channel_page(html, simplified)
    if result is None:
        msg = "Requested Telegram resource was not found"
        raise TelegramNotFoundError(msg)
    return _decode(*result)


def parse_more_page(html: str, *, simplified: bool = False) -> JSONDict:
    """Parse the HTML fragment returned by the "load more" endpoint."""
    return _decode(*native.parse_more_page(html, simplified))


def parse_preview_page(html: str) -> JSONDict | None:
    """Parse the `t.me/<channel>` landing page; None when it is not a channel."""
    page = native.parse_preview_page(html)
    if page is None:
        return None
    data: JSONDict = from_json(page)
    return data
