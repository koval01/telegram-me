# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.0.0] - unreleased

A rewrite of the internals with the same `/v1` API. Existing clients keep
working; see the upgrade guide for the few behaviour changes.

### Changed

- **`POST /v1/feed` is disabled by default.** Enable it with `ENABLE_FEED=true`.
- `POST /v1/previews` returns the channels it could resolve and omits unknown
  ones, instead of failing the whole request with `404`.
- Pages Telegram serves in an unexpected format answer `502` instead of `400`.
- A single malformed post is skipped (and logged) instead of failing the page.
- The server is granian (Rust) started by `telegram-me serve`; the image no
  longer uses `start.sh`. The `Server` header is now `granian`.
- `REDIS_URL` has no default; without it an in-process cache is used.
- Cache keys moved to the `tme:v2:` prefix.
- The feed is ranked natively in Rust (`telegram-me-native`, PyO3):
  engagement relative to each channel, freshness half-life, duplicate stories
  collapsed across channels, topics inferred from the texts and diversity
  re-ranking. `_score` values and the feed order differ from 1.x.

### Added

- GraphQL API at `/graphql` next to REST `/v1`, so clients can select only the
  fields they need: the same data and services as REST, per-request page
  memoization, batching, GraphiQL, stable `extensions.code` errors and
  configurable depth, cost, alias, token and batch limits. Enabled by default
  (`ENABLE_GRAPHQL`); see the GraphQL API docs.
- `/v1/feed` items may carry `_duplicates` (collapsed reposts) and `_topic`
  (shared subject with keywords).
- `forwarded.author`: the signature Telegram shows next to a forward's source.
- Stale-while-revalidate page cache for the feed, parallel fetching of missing
  channels, popularity ranking with decay and a background refresher (embedded
  or `telegram-me worker`) with Redis leader election.
- Cache of rendered responses for `/v1/body` and `/v1/more`; coalescing of
  identical concurrent requests to t.me; a global cap on concurrent upstream
  requests (`TELEGRAM_MAX_CONCURRENCY`).
- Circuit breaker and short timeouts for Redis (`REDIS_TIMEOUT`); size-bounded
  in-memory cache (`MEMORY_CACHE_MAX_MB`); 60 s cache of unknown channels in
  previews.
- New settings: `HOST`, `WEB_CONCURRENCY`, `FORWARDED_ALLOW_IPS`, `ACCESS_LOG`,
  `GRACEFUL_TIMEOUT`, `WORKERS_MAX_RSS_MB`, `METRICS_PORT` (Prometheus),
  `CORS_ALLOW_ORIGINS`, `TELEGRAM_TIMEOUT`, `PREVIEW_CACHE_TTL`, the `FEED_*`
  options, `LOG_LEVEL`, `LOG_FORMAT` (JSON logs).
- `compose.yaml` with a bundled Valkey cache, an optional worker and
  `standard` / `full` / `minimal` presets.
- Rolling multi-server deployments with automatic rollback, migration from the
  1.x container, manual rollback by tag; multi-arch images with SBOM and
  provenance on GHCR and Docker Hub.
- Documentation site on GitHub Pages.
- Offline test suite with recorded t.me pages and contract snapshots of every
  endpoint, strict mypy, ruff, pre-commit.

### Fixed

- Post IDs above 999 999 were rejected with `422`.
- Inline keyboards lost every row after the first.
- `forwarded.name.html` contained a broken `<span>` fragment.
- With Redis unreachable every preview request took 10 seconds.
- The comment count used in feed ranking was never found, while costing one
  extra request to t.me per post (up to ~2 000 per feed request); the dead
  lookup was removed.
- Containers ignored `SIGTERM` (the server was not PID 1): `docker stop` waited
  10 seconds and killed in-flight requests. It now takes well under a second.
- Deployments started containers without a restart policy, so servers did not
  recover after a reboot.
- The rate limiter kept every client IP forever.
- Invalid input to a disabled feature now still answers `422`.

### Performance

- Outgoing requests use one pooled HTTP/2 client in Rust (pyreqwest) instead
  of a new httpx client per request: about 9× less CPU per request.
- HTML is parsed once per page instead of twice; responses are validated once
  and serialized by pydantic-core.
- t.me pages are parsed in Rust (`telegram-me-native`: html5ever, selectors,
  regex) with the GIL released, instead of Python over selectolax. The output
  is byte-for-byte identical to the Python parser; it is 1.3–2× faster per page
  and runs in parallel under load.
- Cached `/v1/body` responses take about 1 ms; a cached 6-channel feed about
  6 ms (82 ms for 5 channels in 1.x).

### Removed

- Fly.io (`fly.toml`), Google App Engine (`app.yaml`), Heroku (`Procfile`,
  `runtime.txt`) configuration, `start.sh`, `docker-compose.yml`,
  `docker-compose.ghcr.yml`, `requirements.txt` and the pylint configuration.
- The `VERSION` setting.

## [1.7] - 2025-10-31

Last release of the 1.x line.

[Unreleased]: https://github.com/koval01/telegram-me/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/koval01/telegram-me/compare/v1.7...v2.0.0
[1.7]: https://github.com/koval01/telegram-me/releases/tag/v1.7
