"""Application settings loaded from environment variables and `.env` files."""

import ipaddress
import os
from functools import cached_property
from typing import Annotated, Literal

from pydantic import (
    Field,
    NonNegativeFloat,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

type CacheMode = Literal["off", "normal", "aggressive"]
type ParserMode = Literal["full", "simplified"]
type LogFormat = Literal["text", "json"]

# How long parsed t.me pages stay cached for each cache mode, in seconds.
# The values match the Cache-Control max-age advertised to Cloudflare.
_PAGE_CACHE_TTL: dict[CacheMode, int] = {"off": 0, "normal": 10, "aggressive": 120}
# Cache-Control header sent to Cloudflare edge requests per cache mode.
_EDGE_CACHE_CONTROL: dict[CacheMode, str | None] = {
    "off": None,
    "normal": "public, max-age=10, stale-while-revalidate=10",
    "aggressive": "public, max-age=120, stale-while-revalidate=240",
}


def _default_workers() -> int:
    return max(1, min(os.process_cpu_count() or 1, 4))


# Private and loopback networks: every peer address a private network uses.
_PRIVATE_NETWORKS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "fc00::/7",
    "::1/128",
]

type CommaList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    """Runtime configuration.

    Every field maps to an upper-case environment variable of the same name,
    for example `enable_feed` is read from `ENABLE_FEED`. Values are also read
    from `.env` and `.env.local` in the working directory.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Server
    host: str = "0.0.0.0"  # noqa: S104 - the server is meant to run inside a container
    port: PositiveInt = 3000
    web_concurrency: PositiveInt = Field(default_factory=_default_workers)
    forwarded_allow_ips: str = "127.0.0.1"
    access_log: bool = False
    graceful_timeout: PositiveInt = 10
    workers_max_rss_mb: PositiveInt | None = None
    """Recycle a worker whose memory exceeds this many MiB."""
    metrics_port: PositiveInt | None = None
    """Expose granian's Prometheus metrics on this port."""

    # API surface
    disable_docs: bool = False
    cors_allow_origins: list[str] = ["*"]

    # Features
    enable_feed: bool = False
    enable_previews: bool = True

    # Protection (both off by default)
    enable_rate_limit: bool = False
    rate_limit_rpm: PositiveInt = 120
    rate_limit_burst: PositiveInt | None = None
    """Requests a client may make at once; RATE_LIMIT_RPM when unset."""
    rate_limit_shared: bool = True
    """With Redis, count each client across every worker and server."""
    enable_pow: bool = False
    pow_secret: SecretStr | None = None
    """Signs proof-of-work challenges; the same on every server, at least 32 characters."""
    pow_difficulty: int = Field(default=18, ge=8, le=32)
    """Leading zero bits each request's hash must have (the work doubles per bit)."""
    pow_challenge_ttl: int = Field(default=300, ge=30, le=3600)
    """Seconds a challenge stays valid (and reusable for new requests)."""
    pow_bind_ip: bool = True
    """Accept a challenge only from the client address it was issued to."""

    # GraphQL (`/graphql`); limits apply to every operation of a request
    enable_graphql: bool = True
    graphql_max_depth: PositiveInt = 10
    """Deepest allowed field nesting (introspection fields excepted)."""
    graphql_max_complexity: PositiveInt = 1500
    """Highest allowed operation cost: 1 per field plus 10 per channel fetched."""
    graphql_max_aliases: PositiveInt = 15
    graphql_max_tokens: PositiveInt = 2000
    """Longest accepted query document, in lexer tokens."""
    graphql_max_batch_size: PositiveInt = 10
    """Most operations accepted in one batched (JSON array) request."""

    # Performance
    cache_mode: CacheMode = "normal"
    parser_mode: ParserMode = "full"
    memory_trim_interval: NonNegativeFloat = 10.0
    """Return freed heap memory to the OS every this many seconds (glibc; 0 disables)."""

    # Upstream requests to t.me
    telegram_base_url: str = Field(default="https://t.me", pattern=r"^https?://[^/?#]+$")
    """Where t.me pages are fetched from: a mirror, or a fake in integration tests."""
    telegram_timeout: PositiveFloat = 10.0
    telegram_max_concurrency: PositiveInt = 32
    upstream_queue_limit: PositiveInt | None = None
    """Requests a worker lets wait for a free upstream slot; beyond it, 503 with
    Retry-After (default: 16 per TELEGRAM_MAX_CONCURRENCY slot)."""
    max_waiting_requests: PositiveInt = 4096
    """Requests a worker lets wait for upstream work at once; beyond it, 503."""
    shared_coalescing: bool = True
    """With Redis, fetch each key once across all workers and servers (a lease)."""
    early_refresh_window: float = Field(default=0.1, ge=0.0, le=0.5)
    """Renew hot entries early, within this fraction of their TTL (0 disables)."""

    # Cache: in-process, in front of Redis when REDIS_URL is set
    redis_url: str | None = None
    redis_timeout: PositiveFloat = 0.5
    """Seconds a Redis command may take; a slower one opens the circuit breaker."""
    redis_connect_timeout: PositiveFloat = 2.0
    """Seconds to open a new Redis connection, TLS handshake included."""
    redis_heartbeat_interval: NonNegativeFloat = 5.0
    """Seconds between this process's heartbeats to Redis (0 disables)."""
    preview_cache_ttl: PositiveInt = 3600
    memory_cache_max_mb: PositiveInt = 64
    memory_cache_max_ttl: PositiveFloat = 10.0
    """With Redis: seconds an in-process copy of a Redis entry is kept at most."""

    # Peers: nodes on a private network share their work directly (off by default)
    enable_peers: bool = False
    peers: CommaList = []
    """Where to find other nodes: comma-separated `host` or `host:port` (DNS names too)."""
    peer_port: int = Field(default=7946, ge=0, le=65535)
    """First peer port (UDP and TCP); each worker takes the next free one (0: any, for tests)."""
    peer_advertise_addr: str | None = None
    """IP other nodes reach this one at (default: the address of the default route)."""
    peer_allow_cidr: CommaList = _PRIVATE_NETWORKS
    """Networks peers may connect from; others are refused before any message is read."""
    peer_secret: SecretStr | None = None
    """Authenticates every peer message; the same on every node, at least 32 characters."""
    peer_encryption: bool = False
    """Also encrypt peer traffic (XChaCha20-Poly1305), for networks you do not trust."""
    peer_timeout: PositiveFloat = 0.05
    """Seconds a peer has to connect and answer before this node does the work itself."""

    # Feed: stale-while-revalidate page cache and popularity-driven refresher
    feed_fresh_ttl: PositiveInt = 60
    feed_stale_ttl: PositiveInt = 900
    feed_refresh_mode: Literal["off", "embedded", "worker"] = "embedded"
    feed_refresh_min_interval: PositiveInt = 30
    feed_refresh_max_interval: PositiveInt = 600
    feed_refresh_top: PositiveInt = 200
    feed_popularity_half_life: PositiveInt = 3600
    feed_half_life_hours: PositiveFloat = 24.0
    """Hours after which a post's freshness halves in the ranking."""
    feed_diversity: float = Field(default=0.5, ge=0.0, le=1.0)
    """0 = pure score order; higher values spread channels and subjects."""
    feed_collapse_duplicates: bool = True
    """Merge the same story posted by several channels into one entry."""

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = "text"

    @field_validator("peers", "peer_allow_cidr", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("peer_allow_cidr")
    @classmethod
    def _check_networks(cls, value: list[str]) -> list[str]:
        for network in value:
            ipaddress.ip_network(network, strict=False)
        return value

    @field_validator("peer_advertise_addr")
    @classmethod
    def _check_address(cls, value: str | None) -> str | None:
        return None if not value else str(ipaddress.ip_address(value))

    @model_validator(mode="after")
    def _require_peer_secret(self) -> "Settings":
        if self.enable_peers:
            secret = self.peer_secret.get_secret_value() if self.peer_secret else ""
            if len(secret) < 32:
                msg = "ENABLE_PEERS needs PEER_SECRET of at least 32 characters"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _require_pow_secret(self) -> "Settings":
        if self.enable_pow:
            secret = self.pow_secret.get_secret_value() if self.pow_secret else ""
            if len(secret) < 32:
                msg = "ENABLE_POW needs POW_SECRET of at least 32 characters"
                raise ValueError(msg)
        return self

    @cached_property
    def page_cache_ttl(self) -> int:
        """Seconds a parsed channel page is cached (0 disables page caching)."""
        return _PAGE_CACHE_TTL[self.cache_mode]

    @cached_property
    def peer_ports(self) -> list[int]:
        """Ports this server's processes take for the peer layer, in order.

        One per worker, plus one so that a respawned worker finds a free port
        while the one it replaces is still shutting down.
        """
        if self.peer_port == 0:
            return [0]
        return [
            port
            for port in range(self.peer_port, self.peer_port + self.web_concurrency + 1)
            if port <= 65535
        ]

    @cached_property
    def edge_cache_control(self) -> str | None:
        """Cache-Control value for Cloudflare edge requests, if any."""
        return _EDGE_CACHE_CONTROL[self.cache_mode]
