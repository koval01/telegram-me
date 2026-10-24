"""Type stubs for the Rust extension module."""

from typing import Self

__version__: str

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
    ) -> Self: ...

def build_feed(pages: list[tuple[str, bytes]], options: RankOptions) -> tuple[bytes, list[int]]:
    """Rank the posts of cached pages and render the `/v1/feed` body.

    `pages` are (requested username, cached page JSON). Returns the response
    JSON and the indices of pages that could not be read (left out).
    """

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

    def __new__(cls, max_bytes: int) -> Self: ...
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

class Overloaded(Exception):  # noqa: N818 - mirrors the Rust name
    """A scheduling bound was reached; `args[0]` is "keys", "waiters" or "queue"."""

class Flights:
    """Work in flight per key, with bounded keys and waiting callers."""

    def __new__(cls, max_keys: int, max_waiters: int) -> Self: ...
    def join(self, key: str, wait: bool) -> object | None:
        """The task in flight for `key`, or None when the caller leads.

        With `wait` the caller counts as a waiter until `leave()`. Raises
        `Overloaded` when a bound is reached.
        """
    def lead(self, key: str, task: object) -> None:
        """Record `task` as in flight for `key`."""
    def leave(self) -> None:
        """A waiting caller is done."""
    def finish(self, key: str, task: object) -> None:
        """Forget `key` if `task` is still the work in flight for it."""
    @property
    def waiters(self) -> int:
        """Number of waiting callers."""
    def __len__(self) -> int: ...

class Gate:
    """Fair admission to upstream slots: per-flow round robin, user before background."""

    def __new__(cls, capacity: int, flow_cap: int, background_cap: int, max_queue: int) -> Self: ...
    def acquire(self, flow: str, background: bool) -> tuple[int, bool]:
        """(ticket, granted); raises `Overloaded` when the queue is full."""
    def release(self, ticket: int) -> list[int]:
        """Give up a held or queued ticket; returns the tickets granted meanwhile."""
    def promote(self, ticket: int) -> list[int]:
        """Raise a queued background ticket to user priority; returns granted tickets."""
    @property
    def retry_after(self) -> float:
        """Suggested client back-off in seconds."""
    @property
    def active(self) -> int:
        """Slots held now."""
    @property
    def queued(self) -> int:
        """Requests waiting for a slot."""
    @property
    def flows(self) -> int:
        """Flows with held or queued requests."""

class EarlyRefresh:
    """Early renewal of hot entries within the last `window` of their TTL, and TTL jitter."""

    def __new__(cls, window: float, seed: int) -> Self: ...
    def claim(self, key: str, age: float, ttl: float, stored_at: float) -> bool:
        """True once per entry (key, stored_at) past its renewal point and still fresh."""
    def jitter(self, ttl: float) -> float:
        """`ttl` shortened by a random part of the renewal window."""

type PeerEvent = tuple[str, int, object, object]

class PeerNode:
    """This process's node of the peer layer; a background thread does the I/O.

    Answers arrive as events: `fileno()` becomes readable and `poll()`
    returns them as `(kind, token, a, b)`:

    - `("got", token, value | None, stored_at | None)`: a `get()` answer;
    - `("value", token, value, stored_at)`, `("failed", token, code, message)`,
      `("unavailable", token, None, None)`: how a `load()` ended;
    - `("serve", token, key, (newer_than, background))`: another node asks
      this one to load `key`; answer with `served()` or `serve_failed()`;
    - `("cast", 0, topic, data)`, `("members", 0, None, None)`.
    """

    def __new__(
        cls,
        *,
        cache: LocalCache,
        secret: bytes,
        ports: list[int],
        bind: str = ...,
        advertise: str | None = None,
        seeds: list[str] = ...,
        seed_ports: list[int] = ...,
        allow: list[str] = ...,
        encrypt: bool = False,
        refresher: bool = True,
        ask_timeout: float = 0.05,
    ) -> PeerNode:
        """Bind the first free port of `ports` (UDP and TCP) and start; OSError if none."""
    @property
    def addr(self) -> str:
        """`ip:port` other nodes reach this one at: its node id."""
    def members(self) -> list[str]:
        """The live members other than this node."""
    def owner(self, route: str) -> str | None:
        """The node owning `route`; None when it is this one (or it is alone)."""
    def is_leader(self) -> bool:
        """Whether this process should run the feed refresher."""
    def get(self, to: str, key: str) -> int:
        """Ask `to` for the cached entry of `key`; returns the event token."""
    def load(
        self, to: str, key: str, newer_than: float, wait: float, background: bool = False
    ) -> int:
        """Ask `to` to load `key` unless it holds a value stored after `newer_than`."""
    def served(self, token: int, keys: list[str]) -> None:
        """Answer a served load with the cached entries of `keys` (requested key first)."""
    def serve_failed(self, token: int, code: int, message: str) -> None:
        """Answer a served load with an error of class `code`."""
    def attach_rate_limiter(self, limiter: RateLimiter, limit: int) -> None:
        """Block clients whose requests across all nodes go over `limit` a minute."""
    def share_counts(self, items: list[tuple[str, int]]) -> None:
        """Add this process's counts per client and send them to every node."""
    def cast(self, topic: str, data: bytes) -> None:
        """Send `data` about `topic` to every other node, best effort."""
    def add_seeds(self, seeds: list[str]) -> None:
        """More places to look for other nodes (`host`, `host:port`)."""
    def poll(self) -> list[PeerEvent]:
        """The events since the last call."""
    def fileno(self) -> int:
        """The socket that becomes readable when `poll()` has events."""
    def stats(self) -> dict[str, int]:
        """Counters: refused, rejected, asked, unanswered, served_gets, served_loads."""
    def close(self) -> None:
        """Leave the cluster and stop the network thread."""
