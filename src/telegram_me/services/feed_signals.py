"""Signals extracted from parsed posts for the native feed ranker.

Only cheap field access happens here; the ranking itself (normalisation,
duplicates, topics, diversity) runs in Rust, see `telegram_me_native`.
"""

from typing import Any

from telegram_me_native import Candidate

POSITIVE_REACTIONS = frozenset({"👍", "❤", "❤️", "😂", "🔥", "💯", "👏", "🎉", "🥰", "⭐"})
NEGATIVE_REACTIONS = frozenset({"👎", "😡", "😢", "🤬", "🤡", "💩"})


def parse_compact_number(value: str | None, default: int = 0) -> int:
    """Parse Telegram's compact counters such as `1.2K`, `3M` or `15 400`."""
    if not value:
        return default
    cleaned = value.replace(" ", "").replace("\N{NO-BREAK SPACE}", "").lower()
    if cleaned.isdigit():
        return int(cleaned)
    try:
        if cleaned.endswith("k"):
            return int(float(cleaned[:-1]) * 1000)
        if cleaned.endswith("m"):
            return int(float(cleaned[:-1]) * 1_000_000)
        return int(cleaned)
    except ValueError:
        return default


def reaction_totals(reacts: list[dict[str, Any]] | None) -> tuple[int, int]:
    """Sum of positive and of negative reaction counters (Stars count as positive)."""
    positive = negative = 0
    for react in reacts or []:
        count = parse_compact_number(str(react.get("count") or "0"))
        emoji = react.get("emoji") or ""
        if react.get("type") == "telegram_stars" or emoji in POSITIVE_REACTIONS:
            positive += count
        elif emoji in NEGATIVE_REACTIONS:
            negative += count
    return positive, negative


def media_counts(content: dict[str, Any]) -> dict[str, int]:
    """Photos, videos, GIFs/stickers and polls attached to a post."""
    counts = {"photos": 0, "videos": 0, "gifs": 0, "polls": 1 if content.get("poll") else 0}
    for media in content.get("media") or []:
        match media.get("type"):
            case "image":
                counts["photos"] += 1
            case "video" | "roundvideo":
                counts["videos"] += 1
            case "gif" | "sticker":
                counts["gifs"] += 1
    return counts


def candidate(channel: str, subscribers: int, post: dict[str, Any]) -> Candidate:
    """Build the ranker input for one raw parsed post."""
    footer = post.get("footer") or {}
    content = post.get("content") or {}
    text = content.get("text") or {}
    positive, negative = reaction_totals(content.get("reacts"))
    return Candidate(
        channel=channel,
        post_id=int(post["id"]),
        published_at=int((footer.get("date") or {}).get("unix") or 0),
        views=parse_compact_number(footer.get("views")),
        subscribers=subscribers,
        positive_reactions=positive,
        negative_reactions=negative,
        text=str(text.get("string") or "") if isinstance(text, dict) else str(text),
        edited=bool(footer.get("edited")),
        **media_counts(content),
    )
