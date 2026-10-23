"""Ranking of feed posts by engagement, content richness and freshness."""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

POSITIVE_REACTIONS = frozenset({"👍", "❤", "❤️", "😂", "🔥", "💯", "👏", "🎉", "🥰"})
NEGATIVE_REACTIONS = frozenset({"👎", "😡", "😢", "🤬", "🤡"})
_MEDIA_WEIGHTS = {"photos": 0.3, "videos": 0.5, "gifs": 0.2, "poll": 0.3}
_SCORE_WEIGHTS = {"engagement": 0.4, "content": 0.2, "freshness": 0.4, "edited": 0.02}


def parse_compact_number(value: str | None, default: int = 0) -> int:
    """Parse Telegram's compact counters such as `1.2K`, `3M` or `15 400`."""
    if not value:
        return default
    cleaned = value.replace(" ", "").lower()
    if cleaned.isdigit():
        return int(cleaned)
    try:
        if "k" in cleaned:
            return int(float(cleaned.replace("k", "")) * 1000)
        if "m" in cleaned:
            return int(float(cleaned.replace("m", "")) * 1_000_000)
        return int(cleaned)
    except ValueError:
        return default


def parse_published_at(value: str) -> datetime:
    """Publication time of a post; falls back to now for unparsable values."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def content_summary(content: dict[str, Any]) -> dict[str, Any]:
    """Text and media counts used by the content score."""
    summary: dict[str, Any] = {"text": "", "photos": 0, "videos": 0, "gifs": 0, "poll": 0}
    text = content.get("text")
    if text:
        summary["text"] = str(text.get("string", "")) if isinstance(text, dict) else str(text)
    for media in content.get("media", []):
        match media.get("type", ""):
            case "image":
                summary["photos"] += 1
            case "video":
                summary["videos"] += 1
            case "sticker":
                summary["gifs"] += 1
    return summary


def reaction_counts(reacts: list[dict[str, Any]] | None) -> dict[str, int]:
    """Map of emoji to positive reaction count."""
    counts: dict[str, int] = {}
    for react in reacts or []:
        emoji = react.get("emoji", "")
        if emoji and (count := parse_compact_number(str(react.get("count", "0")))) > 0:
            counts[emoji] = count
    return counts


@dataclass(slots=True)
class ScoredPost:
    """Inputs of the ranking formula for one post."""

    published_at: datetime
    views: int
    content: dict[str, Any]
    edited: bool = False
    reactions: dict[str, int] = field(default_factory=dict)
    subscribers: int = 1
    comments: int = 0

    def __post_init__(self) -> None:
        self.subscribers = max(self.subscribers, 1)

    def engagement_score(self) -> float:
        """Views, reactions and comments relative to the audience size."""
        positive = sum(self.reactions.get(r, 0) for r in POSITIVE_REACTIONS)
        negative = sum(self.reactions.get(r, 0) for r in NEGATIVE_REACTIONS)
        reaction_pct = (positive - negative) / self.subscribers
        comments_pct = self.comments / self.subscribers
        views_pct = self.views / self.subscribers
        engagement = min(views_pct, 10.0) + min(reaction_pct, 1.0) + min(comments_pct, 0.5)
        return engagement / 3.0

    def content_score(self) -> float:
        """Bonus for media and longer texts."""
        media_bonus = 0.0
        for key, weight in _MEDIA_WEIGHTS.items():
            media_bonus += min(self.content.get(key, 0), 10) * weight
        text_bonus = min(len(self.content.get("text", "")) / 500, 2.0) * 0.1
        return min(media_bonus + text_bonus, 3.0)

    def freshness_score(self, now: datetime) -> float:
        """Decay from 1.0 for brand-new posts towards 0.001 for old ones."""
        hours = (now - self.published_at).total_seconds() / 3600
        if hours <= 24:
            return 1.0 - (0.2 * (hours / 24))
        if hours <= 168:
            return 0.8 * math.exp(-0.8 * (hours / 24 - 1))
        return max(0.001, 0.1 * math.exp(-2.0 * (hours / 168 - 1)))

    def score(self, now: datetime | None = None) -> float:
        """Final score in [0, 10]."""
        now = now or datetime.now(UTC)
        edited_bonus = _SCORE_WEIGHTS["edited"] if self.edited else 0
        base = (
            _SCORE_WEIGHTS["engagement"] * self.engagement_score()
            + _SCORE_WEIGHTS["content"] * self.content_score()
            + edited_bonus
        )
        return min(base * (self.freshness_score(now) + 0.1), 10.0)
