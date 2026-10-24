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

class RateLimiter:
    """Per-client GCRA rate limiter: `burst` requests at once, then `per_minute`."""

    def __init__(self, per_minute: int, burst: int) -> None: ...
    def check(self, key: str, cost: int = 1) -> tuple[bool, int, float]:
        """(allowed, remaining, retry_after_seconds) for one request of `key`."""
    def take_counts(self) -> list[tuple[str, int]]:
        """Accepted cells per client since the previous call."""
    def block(self, key: str, seconds: float) -> None:
        """Reject every request of `key` for the next `seconds`."""
    @property
    def burst(self) -> int:
        """The burst size."""
    def __len__(self) -> int: ...

class ProofOfWork:
    """Stateless proof-of-work challenges (HMAC-signed) and their verification."""

    def __init__(self, secret: bytes, bits: int, ttl: int, bind_ip: bool = True) -> None: ...
    def issue(self, ip: str) -> tuple[str, int, int]:
        """(challenge, bits, expires) for a client at `ip`."""
    def verify(self, header: str | None, method: str, target: str, ip: str) -> str | None:
        """None when the X-PoW header is accepted, else the rejection code."""
    @property
    def bits(self) -> int:
        """Required leading zero bits."""

def solve_pow(challenge: str, method: str, target: str, bits: int) -> str:
    """Find a nonce for `challenge` (reference solver)."""
