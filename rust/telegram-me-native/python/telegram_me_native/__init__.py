"""Native (Rust) core of TelegramMe: t.me page parsing, feed ranking, local cache."""

from telegram_me_native._native import (
    Candidate,
    LocalCache,
    RankedItem,
    RankOptions,
    Topic,
    __version__,
    parse_channel_page,
    parse_more_page,
    parse_preview_page,
    rank,
)

__all__ = [
    "Candidate",
    "LocalCache",
    "RankOptions",
    "RankedItem",
    "Topic",
    "__version__",
    "parse_channel_page",
    "parse_more_page",
    "parse_preview_page",
    "rank",
]
