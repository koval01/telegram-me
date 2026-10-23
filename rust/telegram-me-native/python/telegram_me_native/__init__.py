"""Native (Rust) core of TelegramMe: t.me page parsing and feed ranking."""

from telegram_me_native._native import (
    Candidate,
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
    "RankOptions",
    "RankedItem",
    "Topic",
    "__version__",
    "parse_channel_page",
    "parse_more_page",
    "parse_preview_page",
    "rank",
]
