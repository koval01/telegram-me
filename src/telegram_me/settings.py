"""Application settings loaded from environment variables and `.env` files."""

import os
from functools import cached_property
from typing import Literal

from pydantic import Field, PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    enable_rate_limit: bool = False
    rate_limit_rpm: PositiveInt = 120

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

    # Upstream (t.me)
    telegram_timeout: PositiveFloat = 10.0
    telegram_max_concurrency: PositiveInt = 32

    # Cache backend: Redis when REDIS_URL is set, in-process memory otherwise
    redis_url: str | None = None
    redis_timeout: PositiveFloat = 0.5
    preview_cache_ttl: PositiveInt = 3600
    memory_cache_max_mb: PositiveInt = 64

    # Feed: stale-while-revalidate page cache and popularity-driven refresher
    feed_fresh_ttl: PositiveInt = 60
    feed_stale_ttl: PositiveInt = 900
    feed_refresh_mode: Literal["off", "embedded", "worker"] = "embedded"
    feed_refresh_min_interval: PositiveInt = 30
    feed_refresh_max_interval: PositiveInt = 600
    feed_refresh_top: PositiveInt = 200
    feed_popularity_half_life: PositiveInt = 3600

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = "text"

    @cached_property
    def page_cache_ttl(self) -> int:
        """Seconds a parsed channel page is cached (0 disables page caching)."""
        return _PAGE_CACHE_TTL[self.cache_mode]

    @cached_property
    def edge_cache_control(self) -> str | None:
        """Cache-Control value for Cloudflare edge requests, if any."""
        return _EDGE_CACHE_CONTROL[self.cache_mode]
