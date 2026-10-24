# Configuration

Everything is configured through environment variables. They are also read
from `.env` and `.env.local` in the working directory (later files win, real
environment variables win over both). Names are case-insensitive; booleans
accept `true/false`, `1/0`, `yes/no`, `on/off`.

The source of truth is
[`settings.py`](https://github.com/koval01/telegram-me/blob/main/src/telegram_me/settings.py).

!!! tip "Presets"
    `compose.yaml` loads one of
    [`deploy/presets/*.env`](https://github.com/koval01/telegram-me/tree/main/deploy/presets)
    (`standard`, `full`, `minimal`) before your `.env`. See
    [Quickstart](getting-started/quickstart.md#2-docker-compose-with-a-shared-cache-recommended).

## Overview

| Variable | Default | Group |
|---|---|---|
| [`HOST`](#host) | `0.0.0.0` | Server |
| [`PORT`](#port) | `3000` | Server |
| [`WEB_CONCURRENCY`](#web_concurrency) | CPU count, max 4 | Server |
| [`FORWARDED_ALLOW_IPS`](#forwarded_allow_ips) | `127.0.0.1` | Server |
| [`ACCESS_LOG`](#access_log) | `false` | Server |
| [`GRACEFUL_TIMEOUT`](#graceful_timeout) | `10` | Server |
| [`WORKERS_MAX_RSS_MB`](#workers_max_rss_mb) | — | Server |
| [`METRICS_PORT`](#metrics_port) | — | Server |
| [`DISABLE_DOCS`](#disable_docs) | `false` | API |
| [`CORS_ALLOW_ORIGINS`](#cors_allow_origins) | `["*"]` | API |
| [`ENABLE_PREVIEWS`](#enable_previews) | `true` | Features |
| [`ENABLE_FEED`](#enable_feed) | `false` | Features |
| [`ENABLE_RATE_LIMIT`](#enable_rate_limit) | `false` | Protection |
| [`RATE_LIMIT_RPM`](#rate_limit_rpm) | `120` | Protection |
| [`RATE_LIMIT_BURST`](#rate_limit_burst) | = `RATE_LIMIT_RPM` | Protection |
| [`RATE_LIMIT_SHARED`](#rate_limit_shared) | `true` | Protection |
| [`ENABLE_POW`](#enable_pow) | `false` | Protection |
| [`POW_SECRET`](#pow_secret) | — | Protection |
| [`POW_DIFFICULTY`](#pow_difficulty) | `18` | Protection |
| [`POW_CHALLENGE_TTL`](#pow_challenge_ttl) | `300` | Protection |
| [`POW_BIND_IP`](#pow_bind_ip) | `true` | Protection |
| [`ENABLE_GRAPHQL`](#enable_graphql) | `true` | GraphQL |
| [`GRAPHQL_MAX_DEPTH`](#graphql_max_depth) | `10` | GraphQL |
| [`GRAPHQL_MAX_COMPLEXITY`](#graphql_max_complexity) | `1500` | GraphQL |
| [`GRAPHQL_MAX_ALIASES`](#graphql_max_aliases) | `15` | GraphQL |
| [`GRAPHQL_MAX_TOKENS`](#graphql_max_tokens) | `2000` | GraphQL |
| [`GRAPHQL_MAX_BATCH_SIZE`](#graphql_max_batch_size) | `10` | GraphQL |
| [`CACHE_MODE`](#cache_mode) | `normal` | Performance |
| [`PARSER_MODE`](#parser_mode) | `full` | Performance |
| [`MEMORY_TRIM_INTERVAL`](#memory_trim_interval) | `10` | Performance |
| [`TELEGRAM_TIMEOUT`](#telegram_timeout) | `10` | Upstream |
| [`TELEGRAM_MAX_CONCURRENCY`](#telegram_max_concurrency) | `32` | Upstream |
| [`REDIS_URL`](#redis_url) | — | Cache |
| [`REDIS_TIMEOUT`](#redis_timeout) | `0.5` | Cache |
| [`PREVIEW_CACHE_TTL`](#preview_cache_ttl) | `3600` | Cache |
| [`MEMORY_CACHE_MAX_MB`](#memory_cache_max_mb) | `64` | Cache |
| [`MEMORY_CACHE_MAX_TTL`](#memory_cache_max_ttl) | `10` | Cache |
| [`FEED_FRESH_TTL`](#feed_fresh_ttl) | `60` | Feed |
| [`FEED_STALE_TTL`](#feed_stale_ttl) | `900` | Feed |
| [`FEED_REFRESH_MODE`](#feed_refresh_mode) | `embedded` | Feed |
| [`FEED_REFRESH_MIN_INTERVAL`](#feed_refresh_min_interval) | `30` | Feed |
| [`FEED_REFRESH_MAX_INTERVAL`](#feed_refresh_max_interval) | `600` | Feed |
| [`FEED_REFRESH_TOP`](#feed_refresh_top) | `200` | Feed |
| [`FEED_POPULARITY_HALF_LIFE`](#feed_popularity_half_life) | `3600` | Feed |
| [`FEED_HALF_LIFE_HOURS`](#feed_half_life_hours) | `24` | Feed |
| [`FEED_DIVERSITY`](#feed_diversity) | `0.5` | Feed |
| [`FEED_COLLAPSE_DUPLICATES`](#feed_collapse_duplicates) | `true` | Feed |
| [`LOG_LEVEL`](#log_level) | `INFO` | Logging |
| [`LOG_FORMAT`](#log_format) | `text` | Logging |

## Server

### `HOST`

Interface the HTTP server (and the metrics listener) binds to. Keep
`0.0.0.0` inside containers; use `127.0.0.1` for a bare-metal instance behind
a local reverse proxy.

### `PORT`

Port of the HTTP server. The Docker image listens on `3000`; publish it with
`-p 8000:3000`.

### `WEB_CONCURRENCY`

Number of worker processes. Defaults to the number of CPUs available to the
process, capped at 4. Each worker has its own event loop and, without Redis,
its own memory cache — so use Redis when running more than one.

### `FORWARDED_ALLOW_IPS`

Comma-separated proxy addresses whose `X-Forwarded-For` / `X-Forwarded-Proto`
headers are trusted, or `*` to trust any (only when the port is not reachable
directly). This determines the client IP used by the rate limiter. Example for
nginx on another host: `FORWARDED_ALLOW_IPS=10.0.0.5`.

### `ACCESS_LOG`

Log every request (`true`) — off by default to keep logs quiet at high
request rates.

### `GRACEFUL_TIMEOUT`

Seconds a worker may take to finish in-flight requests after `SIGTERM`
before it is killed.

### `WORKERS_MAX_RSS_MB`

Recycle a worker whose resident memory exceeds this many MiB. A safety net
for memory-constrained hosts; unset by default. A worker uses ~60 MiB idle and
~100 MiB under load, plus up to `MEMORY_CACHE_MAX_MB` for its in-process
cache, so a value of about `MEMORY_CACHE_MAX_MB + 150` only fires on real
trouble.

### `METRICS_PORT`

Expose Prometheus metrics of the server on this port at `/metrics`. See
[Service endpoints](api/service.md#metrics).

## API

### `DISABLE_DOCS`

Hide the Swagger UI (`/`) and `/openapi.json`, and on `/graphql` the GraphiQL
IDE, `GET` queries and schema introspection.

### `CORS_ALLOW_ORIGINS`

JSON list of allowed browser origins, e.g.
`CORS_ALLOW_ORIGINS='["https://example.com"]'`. The default `["*"]` allows
any origin.

## Features

### `ENABLE_PREVIEWS`

Enable `/v1/preview/{channel}` and `/v1/previews`. When disabled they answer
`503`.

### `ENABLE_FEED`

Enable `POST /v1/feed` (and its background refresher). **Off by default**
because it is by far the heaviest endpoint. When disabled it answers `503`.

## Protection

Both are off by default; see [Rate limit and proof of work](api/protection.md) for how they work and what clients send.

### `ENABLE_RATE_LIMIT`

Per-client-IP rate limit for every endpoint (GCRA): a burst of
[`RATE_LIMIT_BURST`](#rate_limit_burst) requests, then
[`RATE_LIMIT_RPM`](#rate_limit_rpm) per minute. Over the limit the API answers
`429` with `Retry-After`. Behind a proxy, set
[`FORWARDED_ALLOW_IPS`](#forwarded_allow_ips) so the real client IP is used.

### `RATE_LIMIT_RPM`

Sustained requests per minute per client IP.

### `RATE_LIMIT_BURST`

Requests a client may make at once before the per-minute rate applies.
Defaults to `RATE_LIMIT_RPM`.

### `RATE_LIMIT_SHARED`

With [`REDIS_URL`](#redis_url), count each client across all workers and
servers: every process adds its counts to Redis once a second and blocks
clients over the limit there. Requests never wait for Redis. With `false`, or
without Redis, each process limits on its own.

### `ENABLE_POW`

Require a [proof of work](api/protection.md#proof-of-work) on every request
(docs, `/healthz` and the challenge endpoint excepted). Needs
[`POW_SECRET`](#pow_secret); `GET /v1/pow/challenge` hands out challenges.

### `POW_SECRET`

Signs challenges (HMAC-SHA256). At least 32 characters, **the same on every
server**, and secret: with it anyone can forge challenges. Generate one with
`openssl rand -hex 32`. Changing it invalidates outstanding challenges.

### `POW_DIFFICULTY`

Leading zero bits each request's SHA-256 must have, from 8 to 32. Every bit
doubles the client's work: 18 is about 0.2 s in Python and 17 ms in native
code per request. See [Choosing the difficulty](api/protection.md#choosing-the-difficulty).

### `POW_CHALLENGE_TTL`

Seconds a challenge stays valid, from 30 to 3600. A client reuses one
challenge for all its requests in that time.

### `POW_BIND_IP`

Accept a challenge only from the IP it was issued to, so solved challenges
cannot be handed out. Turn off for clients whose address changes often.

## GraphQL

See [GraphQL](api/graphql.md#limits) for how the limits are applied.

### `ENABLE_GRAPHQL`

Serve the [GraphQL API](api/graphql.md) at `/graphql`. When disabled the
path answers `404`. GraphiQL, `GET` queries and introspection additionally
follow [`DISABLE_DOCS`](#disable_docs).

### `GRAPHQL_MAX_DEPTH`

Deepest allowed nesting of selections below a root field (the root field is
depth 0; introspection fields are not counted).

### `GRAPHQL_MAX_COMPLEXITY`

Highest allowed operation cost: 1 per selected field plus 10 per channel a
root field may fetch from t.me.

### `GRAPHQL_MAX_ALIASES`

Most aliases allowed in one document.

### `GRAPHQL_MAX_TOKENS`

Longest accepted document, in GraphQL lexer tokens; bounds parsing work.

### `GRAPHQL_MAX_BATCH_SIZE`

Most operations accepted in one batched request (a JSON array). Larger
batches are rejected with `400`.

## Performance

### `CACHE_MODE`

| Value | Page cache | `Cache-Control` for Cloudflare |
|---|---|---|
| `off` | none — every request hits t.me | none |
| `normal` | 10 s | `public, max-age=10, stale-while-revalidate=10` |
| `aggressive` | 120 s | `public, max-age=120, stale-while-revalidate=240` |

Applies to `/v1/body`, `/v1/post` and `/v1/more`. Previews and the feed have
their own settings below.

### `PARSER_MODE`

| Value | Extracted |
|---|---|
| `full` | everything: text with entities, media, polls, buttons, replies, link previews, reactions |
| `simplified` | text (no entities), media and buttons — noticeably cheaper on CPU |

### `MEMORY_TRIM_INTERVAL`

Seconds between returns of freed heap memory to the operating system
(`malloc_trim`, glibc only — the Docker image). After a burst of traffic glibc
otherwise keeps the memory of large short-lived objects for reuse: a worker
under load held ~80 MiB of such free heap. One trim takes about 1 ms. `0`
disables it.

## Upstream

### `TELEGRAM_TIMEOUT`

Total timeout in seconds for one request to t.me (the connect phase is capped
at 5 s). A failed request is retried once on a fresh connection when the
error is a network error.

### `TELEGRAM_MAX_CONCURRENCY`

Maximum simultaneous requests to t.me per worker process. Protects the server
from being rate-limited by Telegram during large feed or previews requests.

## Cache

### `REDIS_URL`

Redis or Valkey URL, e.g. `redis://valkey:6379/0` or
`rediss://user:pass@host:6380/0`. When set, the page cache, preview cache,
feed popularity and the refresher leader lock are shared by every worker and
server. Each worker still answers repeated requests from its in-process cache
first ([`MEMORY_CACHE_MAX_MB`](#memory_cache_max_mb),
[`MEMORY_CACHE_MAX_TTL`](#memory_cache_max_ttl)), so only its misses wait for
a Redis round trip. When unset, the in-process cache is the only one.

A Redis outage never slows requests down: commands time out after
[`REDIS_TIMEOUT`](#redis_timeout) and Redis is then bypassed for 30 seconds
(circuit breaker), the API working on its in-process cache meanwhile.

Each worker keeps up to `2 × TELEGRAM_MAX_CONCURRENCY` (at least 32)
connections to Redis. A burst of commands beyond that, such as several
100-channel feed requests at once, waits up to `REDIS_TIMEOUT` for a free
connection; a command that still gets none is skipped as a cache miss without
opening the circuit breaker.

### `REDIS_TIMEOUT`

Connect and command timeout in seconds, and the longest a command waits for a
free pooled connection.

### `PREVIEW_CACHE_TTL`

Seconds a channel preview stays cached. Unknown channels are cached for 60
seconds regardless.

### `MEMORY_CACHE_MAX_MB`

Memory limit of the in-process cache, per worker, with or without Redis:
entries are stored compressed and the limit counts what they really occupy
(value, key and bookkeeping). Least recently used entries are evicted first.
With the default of 64, a worker under heavy traffic stays around 170 MiB
(the whole container around 230 MiB). With Redis, a worker holds only what
was read or written in the last [`MEMORY_CACHE_MAX_TTL`](#memory_cache_max_ttl)
seconds, usually far less than the limit.

### `MEMORY_CACHE_MAX_TTL`

With Redis: the longest a worker keeps its own copy of a cache entry, in
seconds. A worker sees an entry that another server (or the feed worker)
wrote to Redis at most this long after it was written; the cost is one Redis
read per key and worker this often, for keys that are being read. Copies
never outlive the Redis entry. Without Redis, and while Redis is unavailable,
entries keep their full TTL. See
[Internals → Caching](internals/caching.md#how-the-levels-work-together).

## Feed

All of these only matter with [`ENABLE_FEED=true`](#enable_feed). See
[Internals → Feed](internals/feed.md).

### `FEED_FRESH_TTL`

A cached channel page younger than this (seconds) is used as is.

### `FEED_STALE_TTL`

A cached page older than `FEED_FRESH_TTL` but younger than this is served
immediately while a background refresh replaces it. Older pages are fetched
before answering.

### `FEED_REFRESH_MODE`

| Value | Behaviour |
|---|---|
| `embedded` | each API process runs the refresher; with Redis only one of them (the leader) does the work |
| `worker` | the API does not run it; start `telegram-me worker` separately (needs Redis) |
| `off` | no background refresh; pages are refreshed only by feed requests |

### `FEED_REFRESH_MIN_INTERVAL`

Shortest refresh interval (seconds), used for the most popular channels.

### `FEED_REFRESH_MAX_INTERVAL`

Longest refresh interval, used for channels with almost no demand. A channel
with popularity score *s* is refreshed every
`clamp(MAX / (1 + s), MIN, MAX)` seconds.

### `FEED_REFRESH_TOP`

How many of the most popular channels the refresher looks after.

### `FEED_POPULARITY_HALF_LIFE`

Half-life in seconds of a channel's popularity: a feed request counts half as
much after this time. Channels whose score drops below 0.05 are forgotten.

### `FEED_HALF_LIFE_HOURS`

Hours after which a post's freshness halves in the ranking. Lower values
favour the newest posts more strongly.

### `FEED_DIVERSITY`

From `0` (pure score order) to `1` (strongest spreading of channels and
subjects). See [Internals → Feed](internals/feed.md#diversity).

### `FEED_COLLAPSE_DUPLICATES`

Merge the same story posted by several channels into its best post, listing
the others in `_duplicates`.

## Logging

### `LOG_LEVEL`

`DEBUG`, `INFO`, `WARNING` or `ERROR`.

### `LOG_FORMAT`

`text` for humans, `json` for log shippers (one JSON object per line with
`time`, `level`, `logger`, `message` and any structured extras).
