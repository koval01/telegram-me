# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **A public face for the instance.**
  - `/` is a landing page now: what the service is, a **Try it** box for
    `GET /v1/body/{channel}` with the response, the instance's health, version
    and features, and links to the docs, the API reference, GraphQL and GitHub.
    Static HTML, CSS and a little JS from the package, light and dark,
    without a framework or anything loaded from elsewhere (CSP `'self'`).
  - The documentation is served at `/docs/` (`/docs/es/`, `/docs/ru/`). The
    image ships it, built for that prefix by `scripts/build_docs.py`
    (`DOCS_SITE_DIR=/app/docs`, about 8 MB more image), so it needs no proxy and
    always matches the running version. Without a build `/docs/…` redirects to
    GitHub Pages.
  - `/robots.txt` and `/sitemap.xml`: every docs page in all three languages
    with `hreflang` alternates, generated from the zensical navs at build
    time, on the new `PUBLIC_URL` setting (the deploy sets it from
    `PUBLIC_URL` or `PUBLIC_HOST`).
  - With `ENABLE_POW` the browser solves the proof of work, for the landing
    page and for Swagger's **Try it out**, on Web Workers with a synchronous
    SHA-256 (difficulty 16 in about half a second).

### Changed

- **Swagger UI moved from `/` to `/api`**; `/openapi.json` stays. Old deep
  links (`/#/Channel/...`) are forwarded to `/api` by the landing page.
  `DISABLE_DOCS` hides the new pages as well, and `/robots.txt` then disallows
  everything. The smoke matrix checks the new pages.
- Responses that set their own `Cache-Control` (docs, static files,
  rejections) keep it behind Cloudflare; the `CACHE_MODE` edge default only
  fills in the others.

### Fixed

- The docs' own sitemaps (`/docs/sitemap.xml`, `/docs/es/…`, `/docs/ru/…`)
  carried root-relative locations, which Google Search Console rejects (33
  errors). The instance now serves them with absolute URLs for its public
  address; the docs theme navigates with them as before.

## [2.1.0] - 2026-10-25

Requests to t.me now go out with one browser's TLS, HTTP/2 and headers. Nodes
on a private network can share their work without Redis (optional). Redis
connections hold up at a distance. Every release is gated by smoke tests, a
canary and fleet rollback. The `/v1` and GraphQL contracts are unchanged.

### Added

- **Browser-consistent HTTP client for t.me** (`TELEGRAM_CLIENT=browser`, the
  new default). It is native: wreq on BoringSSL, in `telegram-me-native`. One
  profile (`TELEGRAM_BROWSER_PROFILE=chrome_156` on
  `TELEGRAM_BROWSER_PLATFORM=android`) sets the TLS handshake, the HTTP/2
  settings and the headers, so they all describe the same browser.
  - JA4 and the Akamai HTTP/2 fingerprint change from rustls/hyper
    (`t13d1011h2_61a7ad8aa9b6_…`, `m,s,a,p`) to Chrome's
    (`t13d1516h2_8daaf6152771_…`, `52d84b11…`); see
    `docs/internals/http-client.md`.
  - The "load more" call goes out as the page's XHR (`sec-fetch-mode: cors`,
    `origin`, `referer`, Chromium's header order), not as a navigation.
  - `TELEGRAM_CLIENT=reqwest` keeps pyreqwest; it is not used in production.
  - `scripts/fingerprint.py` measures both clients against a TLS echo service.
  - **Our own profile of the current stable Chrome**, `chrome_156`, is the
    default; the wreq-util profiles (newest `chrome_149`) stay selectable. It
    claims Chrome 156 in the `User-Agent` and `sec-ch-ua` (whose GREASE brand
    is derived from the version as Chromium does), sends `image/jxl` in
    `accept` as Chrome 155+ does, and the HTTP/2 `HEADERS` priority weight
    256 that Chrome sends (wreq-util sends 220). The TLS handshake is
    unchanged: the BoringSSL that wreq builds can not offer the ML-DSA
    signature algorithms (it rejects them), a GREASE signature algorithm or
    the `trust_anchors` extension that Chrome 155 sends; see
    `docs/internals/http-client.md`.
  - **Cookies like a browser**: the client keeps the cookie t.me sets
    (`stel_ssid`) and sends it back, `cookie` placed where Chromium puts it.
    Each worker has one browser identity (jar, connections, TLS tickets),
    renewed after `TELEGRAM_SESSION_LIFETIME` (new, default 4 hours ±25 %),
    when its cookies expire, or after a 4xx other than 404. Cookies stay in
    the native client: never in a response, a log or the cache.
- **Peer layer** (`ENABLE_PEERS`, off by default): nodes on a private network
  share their work directly, groupcache-style, with or without Redis. See
  [Deployment → Peer layer](https://koval01.github.io/telegram-me/deployment/peers/).
  - Each channel's keys have one owner among the live nodes (rendezvous
    hashing). Only the owner fetches and renders them; on a miss the other
    nodes ask it and keep its answer for the remaining TTL. With peers up this
    replaces the Redis lease.
  - The owner answers cache reads natively, without Python: 0.18 ms for a
    56 KB page, against 95 ms through a Redis 90 ms away.
  - Membership is SWIM gossip (`foca`) over UDP; requests use one multiplexed
    TCP connection per peer. Nodes find each other through `PEERS`
    (addresses or DNS names) and the Redis heartbeat markers, which now carry
    `"peer"`.
  - Every frame is authenticated (HMAC-SHA256 with `PEER_SECRET`, timestamp,
    nonce against replays) and optionally encrypted (`PEER_ENCRYPTION`,
    XChaCha20-Poly1305); peers outside `PEER_ALLOW_CIDR` are refused.
  - A slow or unreachable peer never fails a request: after `PEER_TIMEOUT`
    (50 ms) the node does the work itself.
  - Without Redis, nodes share rate-limit counts and feed popularity, and the
    live node with the lowest address runs the feed refresher.
  - New settings: `ENABLE_PEERS`, `PEERS`, `PEER_PORT`, `PEER_ADVERTISE_ADDR`,
    `PEER_ALLOW_CIDR`, `PEER_SECRET`, `PEER_ENCRYPTION`, `PEER_TIMEOUT`.
  - `deploy/bootstrap-server.sh` opens the peer ports to the private network
    (`PEER_CIDR`); the deploy workflow enables the layer with the
    `ENABLE_PEERS` variable and the `PEER_SECRET` secret.
  - The standalone feed worker runs with `ENABLE_PEERS` instead of
    `REDIS_URL` as well.
- Stricter checks on every commit and pull request (`make lint`, `make
  audit`, `make image-scan`, all in `make check`): clippy restriction lints
  and no panicking slices in the parser; cargo-deny and cargo-machete; more
  mypy error codes and ruff rule families; import-linter layer contracts;
  vulture; `uv audit`; gitleaks; zizmor; hadolint; typos; trivy on the built
  image; dependency review on pull requests; OpenSSF Scorecard.
- `REDIS_CONNECT_TIMEOUT` (default 2 s): seconds to open a Redis connection,
  TLS handshake included.
- **Deployment gates.** Every server runs the smoke test (status codes and
  content) inside the new container after its health check and goes back to
  the previous image when it fails. Server 1 is a canary: it serves real
  traffic for `CANARY_SOAK` seconds and is verified again before the other
  servers follow. The rollout ends with the smoke test through the public URL
  (`PUBLIC_URL` or `https://PUBLIC_HOST`, with `CF_BYPASS_KEY`), and a
  failure after the first server rolls back every server that took the
  release (`deploy/rollout.sh`, `remote-deploy.sh --verify` and `--rollback`).
  Manual deploys can skip the smoke test while t.me is down.
- CI runs the built image as two servers with two workers each and a real
  Valkey (`tests/integration`): shared cache, cross-process fetch lease,
  shared rate limit, proof of work, a rolling upgrade from the release in
  production and a Valkey outage. `deploy/remote-deploy.sh` is tested with
  real Docker Compose (upgrade, unhealthy and broken releases, rollback, the
  Caddy proxy) and the fleet logic with stub servers (`tests/deploy`).
- **Guards** workflow: breaking changes of the REST (OpenAPI) and GraphQL
  contract against the base of a pull request fail it unless it is labelled
  `breaking-change` (`scripts/api_contract.py`, `make contract`); coverage
  floors may only go up (`scripts/coverage_floor.py`).
- Coverage floors in CI: Python 92.5 % (`fail_under`), Rust 96 % lines
  (`cargo llvm-cov`).
- Property-based tests (hypothesis): no 500 whatever t.me returns, the
  protection middleware on any input, feed invariants. Hostile-HTML tests of
  the Rust parsers (mutated pages, deep nesting, linear parse time) and a
  `cargo fuzz` target.
- **Nightly** workflow: the production image against live t.me, production
  through the public URL, 10 minutes of fuzzing and long property runs; a
  failure opens an issue labelled `nightly`.
- The smoke matrix checks the content of the main responses, not only status
  codes, and uses recorded pages only, so it also runs against a fake t.me.
- `TELEGRAM_BASE_URL`: where t.me pages are fetched from (a mirror, or a fake
  t.me in tests).
- **Redis heartbeat** (`REDIS_HEARTBEAT_INTERVAL`, default 5 s). Every worker
  writes its marker `tme:v2:node:<host>:<pid>` with a TTL of three intervals.
  - It keeps a connection to Redis open.
  - It notices outages within one interval.
  - It shows which processes are running.
  - It probes while the circuit breaker is open: the breaker closes as soon as
    Redis answers again, instead of after the full 30 s cooldown.

### Changed

- **Request headers to t.me come from the browser profile** for both clients,
  not from a hand-written Chrome 141 set: Chrome 149 values, Chrome's header
  order and a quoted `sec-ch-ua-platform`. `cache-control` and `dnt`, which
  Chrome does not send on a first visit, are gone.
- **The Docker build stage needs cmake, clang and git** for BoringSSL. A cold
  build takes about 75 s longer on native arm64, and about 11 min longer for
  arm64 under QEMU, as the release workflow builds it. The runtime image grows
  by 3–7 MB.
- The image runs as `USER 10001:10001` (the same `app` user, by number).
- The image's health check probes every 2 s during the first 30 s, so a new
  container is healthy (and a deployment done) within seconds instead of 30 s.
- **The Redis circuit breaker opens after three failures in a row within
  10 seconds** instead of after the first one. Any command that reaches Redis
  resets the count. A single slow TLS connect to a distant Redis, or one lost
  packet, no longer takes the shared cache, the shared rate limit and the
  cross-server lease offline for 30 seconds. Failures below the threshold are
  logged at `INFO`.
- Release images build each platform on a runner of its own architecture
  (`ubuntu-24.04-arm` for `linux/arm64`) instead of arm64 under QEMU, and
  merge the per-platform digests into one manifest list with the same tags,
  provenance and SBOM. Each platform has its own build cache.

### Fixed

- **Redis no longer keeps dropping out when it is far away.** Opening a
  connection used the 0.5 s command timeout, and a TLS connection to a Redis
  90 ms away takes 0.3–0.7 s. After any error, every reconnect timed out and
  reopened the circuit breaker, which bypassed Redis for 30 s every 30–60 s in
  production. Connections now get `REDIS_CONNECT_TIMEOUT` (default 2 s), and
  each worker opens its first connection at startup.
- Deeply nested markup (tens of thousands of levels) no longer overflows the
  stack while a post is serialized, which aborted the worker process.
- The nightly fuzzing job builds for the host target. The prebuilt
  cargo-fuzz defaulted to musl, which the nightly toolchain lacks (issue #217).

### Security

- ctutils 0.4.5 (RUSTSEC-2026-0385, non-constant-time comparisons under the
  proof-of-work HMAC). The crate declared Rust 1.85, which kept the resolver
  on the vulnerable release; it now declares 1.99, what it is built with.
- The image no longer contains pip, whose vendored urllib3, msgpack and
  setuptools had fixable high-severity advisories; it was never used at run
  time.
- The deploy workflow passes only the secrets it needs to the reusable
  deployment, reads repository variables from the environment instead of
  expanding them into the script, and keeps no git credentials after checkout.
- The Dockerfile's base images (uv, Rust, Python) are pinned by digest as well
  as by tag; Dependabot updates both. OpenSSF Scorecard flagged the tag-only
  `FROM` lines (Pinned-Dependencies).
## [2.0.0] - 2026-10-24

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
- The license is now Apache-2.0 (was GPL-3.0). `NOTICE` states that the
  project is not affiliated with Telegram and that returned content belongs to
  its authors.
- The feed is ranked natively in Rust (`telegram-me-native`, PyO3):
  engagement relative to each channel, freshness half-life, duplicate stories
  collapsed across channels, topics inferred from the texts and diversity
  re-ranking. `_score` values and the feed order differ from 1.x.
- **The rate limit is native (Rust) and shared.** It uses GCRA (a burst of
  `RATE_LIMIT_BURST`, then `RATE_LIMIT_RPM` per minute) instead of a Python
  sliding window. With Redis, a client is counted across all workers and
  servers without requests ever waiting for Redis.
  - `Retry-After` is now the actual wait instead of a fixed 60 s.
  - The `429` body is JSON (`{"detail": "Rate limit exceeded"}`).
  - CORS preflights are no longer counted, and `429` responses carry CORS
    headers.

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
- Circuit breaker and short timeouts for Redis (`REDIS_TIMEOUT`); compressed
  in-memory cache bounded by real memory (`MEMORY_CACHE_MAX_MB`); 60 s cache of
  unknown channels in previews.
- New settings: `HOST`, `WEB_CONCURRENCY`, `FORWARDED_ALLOW_IPS`, `ACCESS_LOG`,
  `GRACEFUL_TIMEOUT`, `WORKERS_MAX_RSS_MB`, `METRICS_PORT` (Prometheus),
  `CORS_ALLOW_ORIGINS`, `TELEGRAM_TIMEOUT`, `PREVIEW_CACHE_TTL`, the `FEED_*`
  options, `MEMORY_TRIM_INTERVAL`, `LOG_LEVEL`, `LOG_FORMAT` (JSON logs).
- `compose.yaml` with a bundled Valkey cache, an optional worker and
  `standard` / `full` / `minimal` presets.
- Servers can sit straight behind Cloudflare: with the `PUBLIC_HOST`
  variable every server also runs Caddy on port 443 (Cloudflare Origin CA
  certificate from the `TLS_CERT`/`TLS_KEY` secrets, or a self-signed one) and
  the API only listens on localhost. `deploy/bootstrap-server.sh` prepares a
  fresh Ubuntu server (Docker, swap, Oracle Cloud firewall). Deployment logs
  mask the server addresses.
- Rolling multi-server deployments with automatic rollback, migration from the
  1.x container, manual rollback by tag; multi-arch images with SBOM and
  provenance on GHCR and Docker Hub.
- Documentation site on GitHub Pages.
- Offline test suite with recorded t.me pages and contract snapshots of every
  endpoint, strict mypy, ruff, pre-commit.
- **Optional proof of work** (`ENABLE_POW`). Each request carries an
  `X-PoW: <challenge>:<nonce>` header, and its SHA-256 must start with
  `POW_DIFFICULTY` zero bits (18 by default).
  - Challenges come from `GET /v1/pow/challenge` or any rejection. They are
    stateless and HMAC-signed (`POW_SECRET`), bound to the client IP
    (`POW_BIND_IP`), and reusable until they expire (`POW_CHALLENGE_TTL`).
  - Each solution is tied to its method and target, and accepted once.
  - Rejections answer `428`, `400` or `403` with a fresh challenge.
  - Verification is native (Rust) and takes under a microsecond.
- `RATE_LIMIT_BURST` and `RATE_LIMIT_SHARED`.
- `scripts/audit.py`: wall time, CPU time and Python allocations per
  endpoint, an event-loop stall test, the cost of each stage of the cold page
  pipeline and the memory of a feed loop. The findings and the plan for the
  rest of the hot path are in `docs/internals/rust-roadmap.md`.
- The contract snapshots also pin every REST response byte for byte
  (`tests/snapshots/raw_digests.json`), and a synthetic feed covers
  duplicates and malformed pages.
- `UPSTREAM_QUEUE_LIMIT` (default 16 × `TELEGRAM_MAX_CONCURRENCY`) and
  `MAX_WAITING_REQUESTS` (default 4096): per-worker bounds beyond which
  requests are shed with `503`.
- `SHARED_COALESCING` (default `true`): the cross-process fetch lease.
- `EARLY_REFRESH_WINDOW` (default `0.1`): how close to expiry popular entries
  are renewed in the background; `0` disables it.
- `scripts/coalescing_load.py`: duplicate-heavy load over several worker
  processes sharing one Redis.
- `MEMORY_CACHE_MAX_TTL` (default 10 s): with Redis, the longest a worker
  keeps its own copy of an entry, which bounds how late it sees an entry
  another server or the feed worker wrote.

### Fixed

- Post IDs above 999 999 were rejected with `422`.
- Inline keyboards lost every row after the first.
- `forwarded.name.html` contained a broken `<span>` fragment.
- With Redis unreachable every preview request took 10 seconds.
- A few concurrent feed or previews requests could exhaust the Redis
  connection pool, which opened the circuit breaker and sent every request to
  t.me for 30 seconds. Bursts now wait briefly for a pooled connection
  (`2 × TELEGRAM_MAX_CONCURRENCY` per worker), and an exhausted pool no longer
  counts as a Redis outage.
- The comment count used in feed ranking was never found, while costing one
  extra request to t.me per post (up to ~2 000 per feed request); the dead
  lookup was removed.
- Containers ignored `SIGTERM` (the server was not PID 1): `docker stop` waited
  10 seconds and killed in-flight requests. It now takes well under a second.
- Deployments started containers without a restart policy, so servers did not
  recover after a reboot.
- The rate limiter kept every client IP forever.
- Invalid input to a disabled feature now still answers `422`.
- A feed refresher tick over ~200 channels stalled the event loop for about a
  second (pages were parsed and validated on it), long enough for a Redis
  command to time out and open the circuit breaker for 30 s. Pages are now
  parsed, validated and rendered on two dedicated threads, and the refresher
  refreshes at most 16 channels at a time: worst event-loop lag under load
  ~1 s → about 30 ms (rarely ~120 ms), no breaker trips.

### Performance

- Outgoing requests use one pooled HTTP/2 client in Rust (pyreqwest) instead
  of a new httpx client per request: about 9× less CPU per request.
- HTML is parsed once per page instead of twice; responses are validated once
  and serialized by pydantic-core.
- t.me pages are parsed in Rust (`telegram-me-native`: html5ever, selectors,
  regex) with the GIL released, instead of Python over selectolax. The output
  is byte-for-byte identical to the Python parser; it is 1.3–2× faster per page
  and runs in parallel under load.
- Memory: cache values are compressed UTF-8 bytes counted at their real size
  (a 64 MB cache used to grow to ~260 MiB), workers return freed heap memory
  to the OS (`malloc_trim`), and the server process no longer imports the
  application. One worker: ~110 MiB idle instead of ~150; under heavy traffic
  ~230 instead of ~460 MiB with the memory cache and ~155 instead of ~300 MiB
  with Redis.
- Cached `/v1/body` responses take about 1 ms; a cached 6-channel feed about
  6 ms (82 ms for 5 channels in 1.x).
- **The feed is built natively.** `POST /v1/feed` hands the cached pages to
  the Rust extension and gets the response back in one call with the GIL
  released (`telegram_me_native.build_feed`): reading the pages, the ranking
  signals, ranking and rendering. A 100-channel feed takes about half the
  time (84 → 43 ms in process), allocates half as much Python memory, and no
  longer stalls the worker's other requests: a cached `/v1/body` next to a
  feed loop went from 62 to 10 ms at p99. Responses are byte-for-byte the
  same. Resident memory is unchanged within noise.
- **Identical concurrent requests do the work once, across all workers and
  servers.** With Redis, the process that fetches a page from t.me takes a
  short lease (extended while it runs); every other worker and server waits
  for its result in the cache instead of fetching the same page. Under a
  duplicate-heavy load on 6 processes, upstream requests dropped from 15–16
  to 2.8 per distinct page over 25 s (one per TTL period, 75 instead of
  ~420), also with Redis 90 ms away, and CPU per request from 1.4 ms to
  0.27–0.29 ms. When Redis is slow or down, each worker still coalesces on
  its own.
- Rendering is shared too: concurrent misses of `/v1/body` render the page
  once (it was rendered once per request), `/v1/post` responses are cached
  rendered (a hit takes 0.07 ms instead of 0.24 ms), and identical
  `/v1/feed` requests (REST or GraphQL, channel names in any case) are built
  once, and decoded once for GraphQL.
- Popular pages are renewed in the background shortly before they expire,
  so they no longer expire under load and nobody waits for t.me at expiry.
- The per-worker cap on requests to t.me (`TELEGRAM_MAX_CONCURRENCY`) is
  shared fairly: round-robin over channels and feed requests instead of
  first come, first served, and background refreshes only use spare
  capacity. The feed refresher no longer fetches pages that any process
  fetched moments ago.
- Requests beyond the new queue bounds are answered `503` with
  `Retry-After` instead of queueing without limit.
- **Repeated requests no longer wait for Redis.** Each worker keeps an
  in-process cache in front of Redis: a repeated `/v1/body`, `/v1/post`,
  `/v1/more` or `/v1/preview` takes 0.1–0.25 ms instead of one Redis round
  trip (~95 ms with Upstash). Misses read Redis once (`GET` and `PTTL` in one
  pipeline) and keep a copy for the entry's remaining TTL, at most
  `MEMORY_CACHE_MAX_TTL` seconds; writes go to both levels. While Redis is
  down the in-process cache works alone.
- The in-process cache is native (Rust, `moka`, zstd) instead of a Python
  `OrderedDict`: LRU bounded by `MEMORY_CACHE_MAX_MB` of real memory as
  before, per-entry expiry, and a hit is up to a third faster without Redis.
- With Redis, each worker now also uses up to `MEMORY_CACHE_MAX_MB` (64 MiB by
  default) for the in-process cache; in practice only what was read in the
  last `MEMORY_CACHE_MAX_TTL` seconds.

### Removed

- Fly.io (`fly.toml`), Google App Engine (`app.yaml`), Heroku (`Procfile`,
  `runtime.txt`) configuration, `start.sh`, `docker-compose.yml`,
  `docker-compose.ghcr.yml`, `requirements.txt` and the pylint configuration.
- The `VERSION` setting.

## [1.7] - 2025-10-31

Last release of the 1.x line.

[Unreleased]: https://github.com/koval01/telegram-me/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/koval01/telegram-me/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/koval01/telegram-me/compare/v1.7...v2.0.0
[1.7]: https://github.com/koval01/telegram-me/releases/tag/v1.7
