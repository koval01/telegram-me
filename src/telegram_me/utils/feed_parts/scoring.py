import asyncio
import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional


class ScoredPost:  # pylint: disable=too-many-instance-attributes
    """Post scoring model with cached result."""

    __slots__ = (
        "post_id",
        "channel_name",
        "username",
        "published_at",
        "views",
        "content",
        "edited",
        "reactions",
        "subscribers",
        "comments",
        "_cached_score",
        "_score_timestamp",
    )

    POSITIVE_REACTIONS = {"👍", "❤", "❤️", "😂", "🔥", "💯", "👏", "🎉", "🥰"}
    NEGATIVE_REACTIONS = {"👎", "😡", "😢", "🤬", "🤡"}

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        post_id: int,
        channel_name: str,
        username: str,
        published_at: datetime,
        views: int,
        content: Dict[str, Any],
        edited: bool = False,
        reactions: Optional[Dict[str, int]] = None,
        subscribers: int = 1,
        comments: int = 0,
    ) -> None:
        """Initialize score model values."""
        self.post_id = post_id
        self.channel_name = channel_name
        self.username = username
        self.published_at = published_at
        self.views = views
        self.content = content
        self.edited = edited
        self.reactions = reactions or {}
        self.subscribers = max(subscribers, 1)
        self.comments = comments
        self._cached_score: Optional[float] = None
        self._score_timestamp: Optional[float] = None

    def engagement_score(self) -> float:
        """Compute engagement component."""
        subscribers = self.subscribers
        positive = sum(self.reactions.get(r, 0) for r in self.POSITIVE_REACTIONS)
        negative = sum(self.reactions.get(r, 0) for r in self.NEGATIVE_REACTIONS)

        reaction_pct = (positive - negative) / subscribers
        comments_pct = self.comments / subscribers
        views_pct = self.views / subscribers
        engagement_score = min(views_pct, 10.0) + min(reaction_pct, 1.0) + min(comments_pct, 0.5)
        return engagement_score / 3.0

    def content_score(self) -> float:
        """Compute content quality component."""
        weights = {"photos": 0.3, "videos": 0.5, "gifs": 0.2, "poll": 0.3}

        media_bonus = 0
        for key, weight in weights.items():
            media_count = min(self.content.get(key, 0), 10)
            media_bonus += media_count * weight

        text_len = len(self.content.get("text", ""))
        text_bonus = min(text_len / 500, 2.0) * 0.1
        return min(media_bonus + text_bonus, 3.0)

    def freshness_score(self, current_time: Optional[datetime] = None) -> float:
        """Compute freshness decay component."""
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        published_at = self.published_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        hours_since_post = (current_time - published_at).total_seconds() / 3600
        if hours_since_post <= 24:
            return 1.0 - (0.2 * (hours_since_post / 24))
        if hours_since_post <= 168:
            days = hours_since_post / 24
            return 0.8 * math.exp(-0.8 * (days - 1))
        weeks = hours_since_post / 168
        return max(0.001, 0.1 * math.exp(-2.0 * (weeks - 1)))

    def score(
        self,
        current_time: Optional[datetime] = None,
        weights: Optional[Dict[str, float]] = None,
        use_cache: bool = True,
    ) -> float:
        """Compute final weighted score."""
        if use_cache and self._cached_score is not None:
            cache_time = getattr(self, "_score_timestamp", 0)
            if asyncio.get_event_loop().time() - cache_time < 60:
                return self._cached_score

        weights = weights or {
            "engagement": 0.4,
            "content": 0.2,
            "freshness": 0.4,
            "edited": 0.02,
        }

        engagement = self.engagement_score()
        content = self.content_score()
        freshness = self.freshness_score(current_time)
        edited_bonus = weights["edited"] if self.edited else 0

        base_score = (
            weights["engagement"] * engagement
            + weights["content"] * content
            + edited_bonus
        )
        raw_score = base_score * (freshness + 0.1)
        final_score = min(raw_score, 10.0)

        if use_cache:
            self._cached_score = final_score
            self._score_timestamp = asyncio.get_event_loop().time()

        return final_score
