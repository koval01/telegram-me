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

def parse_channel_page(html: str, simplified: bool) -> tuple[str, list[tuple[str, str]]] | None:
    """Parse `t.me/s/<channel>`: (page JSON, skipped posts), None if not a channel."""

def parse_more_page(html: str, simplified: bool) -> tuple[str, list[tuple[str, str]]]:
    """Parse a "load more" fragment: (page JSON, skipped posts)."""

def parse_preview_page(html: str) -> str | None:
    """Parse a landing page; None when it is not a channel."""

class LocalCache:
    """In-process cache of byte values (moka, LRU), bounded by real memory.

    Values are stored zstd-compressed when that makes them smaller; each entry
    expires after its own TTL and carries the wall-clock time it was stored at.
    """

    def __new__(cls, max_bytes: int) -> LocalCache: ...
    def get(self, key: str) -> tuple[bytes, float] | None:
        """(value, stored_at) for `key`, or None when missing or expired."""
    def set(self, key: str, value: bytes, ttl: float, stored_at: float) -> None:
        """Store `value` for `ttl` seconds (nothing for ttl <= 0 or oversized values)."""
    def fill(self, key: str, value: bytes, ttl: float, stored_at: float) -> bool:
        """Store `value` unless an entry stored at or after `stored_at` is held."""
    def clear(self) -> None:
        """Drop every entry."""
    @property
    def size(self) -> int:
        """Bytes held by the entries: keys, stored values and bookkeeping."""
    @property
    def max_bytes(self) -> int:
        """The memory limit in bytes."""
    def __len__(self) -> int: ...
