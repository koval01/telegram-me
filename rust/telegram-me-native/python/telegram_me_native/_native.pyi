"""Type stubs for the Rust extension module."""

__version__: str

class Candidate:
    """One post to rank. Counters are already parsed (`1.2K` -> 1200)."""

    def __new__(
        cls,
        *,
        channel: str,
        post_id: int,
        published_at: int,
        views: int = 0,
        subscribers: int = 0,
        positive_reactions: int = 0,
        negative_reactions: int = 0,
        photos: int = 0,
        videos: int = 0,
        gifs: int = 0,
        polls: int = 0,
        text: str = "",
        edited: bool = False,
    ) -> Candidate: ...

class RankOptions:
    """Ranking parameters."""

    def __new__(
        cls,
        *,
        now: int,
        half_life_hours: float = 24.0,
        diversity: float = 0.5,
        collapse_duplicates: bool = True,
        duplicate_distance: int = 20,
        duplicate_containment: float = 0.7,
        duplicate_min_tokens: int = 10,
        topic_threshold: float = 0.3,
        topic_keywords: int = 5,
    ) -> RankOptions: ...

class RankedItem:
    """A ranked post; `index` points into the candidate list."""

    @property
    def index(self) -> int: ...
    @property
    def score(self) -> float: ...
    @property
    def duplicates(self) -> list[int]: ...
    @property
    def topic(self) -> int | None: ...

class Topic:
    """A group of posts about the same subject."""

    @property
    def keywords(self) -> list[str]: ...
    @property
    def members(self) -> list[int]: ...

def rank(candidates: list[Candidate], options: RankOptions) -> tuple[list[RankedItem], list[Topic]]:
    """Rank candidates; returns (items in feed order, topics)."""
