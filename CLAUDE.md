# CLAUDE.md

Guidance for Claude Code and other AI coding agents working in this repository.
Humans: see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[documentation](https://tme.koval-dev.org/docs/).

## What this is

TelegramMe turns public Telegram channel pages (`t.me/s/<channel>`) into JSON.
It serves REST `/v1` and GraphQL `/graphql`. Demo: https://tme.koval-dev.org
(behind Cloudflare bot protection; it is not a free public instance).

- Python 3.13+ (the image runs 3.14), FastAPI on granian, Strawberry GraphQL,
  Redis (Valkey in tests) as the shared cache, and a Rust extension that does
  the heavy work.
- Apache-2.0. `NOTICE` carries the disclaimer that the project is not
  affiliated with Telegram.
- The maintainer writes in Russian; answer in Russian. Code, comments, docs,
  commits, PRs and issues are in English (docs also in es/ru).

## Architecture rule: heavy work is native

- Anything on the hot path or CPU/memory heavy belongs in Rust, in the
  `rust/telegram-me-native` crate (PyO3, maturin, abi3 for Python 3.13+).
  Native code we write is **Rust only**. Ready-made native components may be
  C, C++ or Rust: pyreqwest, granian, pydantic-core, hiredis, zstd.
- Python (`src/telegram_me`) is glue: wiring, business rules, orchestration
  and scripts. If a change adds per-request Python work, consider the crate
  first.
- The crate already holds:
  - the t.me page parser (`parse/`: `dom.rs`, `pages.rs`, `post.rs`,
    `media.rs`, `entities.rs`);
  - feed building and ranking (`feed.rs`, `rank.rs`, `simhash.rs`,
    `topics.rs`, `text.rs`);
  - the in-process L1 cache (`cache.rs`, moka plus zstd);
  - the rate limiter (`ratelimit.rs`, GCRA) and proof of work (`pow.rs`);
  - the request-scheduling core (`sched.rs`);
  - the HTTP client to t.me (`http.rs`, `http/chrome.rs`: wreq on BoringSSL
    with a Chrome fingerprint and a cookie jar);
  - the optional peer layer (`peer/`: SWIM membership on `foca`,
    authenticated frames, rendezvous hashing, the tokio network thread);
    its Python bridge is `src/telegram_me/peers.py`.
- The Python bindings live in `src/python.rs` behind the `python` feature;
  the crate also builds and tests without it (clippy runs both feature sets).
- `docs/internals/rust-roadmap.md` plans what moves next.
- **The GIL.** Release it (`py.detach`) only around work long enough to be
  worth it. Short calls made from the event loop keep it: see the comments in
  `src/python.rs` for the measurements behind that.
- After pulling or editing anything under `rust/`, run `make native`. A stale
  extension fails at import (`cannot import name '...' from
  'telegram_me_native._native'`).

## How a request flows

- **Process:** `telegram-me serve` starts granian (`server.py`), which runs
  `server.asgi()` in each worker. `telegram-me worker` runs only the feed
  refresher (needs Redis), and `telegram-me version` prints the version.
- **ASGI wrapping, outermost first:**
  1. `keep_socket_peer` records the TCP peer.
  2. granian's `wrap_asgi_with_proxy_headers` replaces `scope["client"]`
     with the `X-Forwarded-For` address, but only from `FORWARDED_ALLOW_IPS`.
  3. Then the app's middleware, in the order `app.py` adds it: CORS, then
     `ResponseHeadersMiddleware` (`X-App-Node`, `X-Process-Time`, the edge
     `Cache-Control`), then `ProtectionMiddleware` (rate limit, then proof of
     work; only when one of them is enabled).
- **Routes:**
  - `GET /healthz` (`Cache-Control: no-store`);
  - `GET /v1/body/{channel}`, `/v1/more/{channel}/{direction}/{position}`,
    `/v1/post/{channel}/{identifier}`, `/v1/preview/{channel}`;
  - `POST /v1/previews` and `POST /v1/feed`;
  - `/graphql` with the root fields `body`, `more`, `post`, `preview`,
    `previews` and `feed`;
  - `GET /v1/pow/challenge`, only with `ENABLE_POW`;
  - the site (`api/site.py`): `/` landing page, `/api` Swagger UI,
    `/openapi.json`, `/docs/…` (the docs built into the image, en/es/ru),
    `/robots.txt`, `/sitemap.xml` and `/static/…`.
- **Upstream:** `services/` fetch through `telegram/client.py`.
  - `BrowserFetcher` (native wreq, profile `chrome_156` on Android by default)
    keeps t.me's `stel_ssid` cookie per worker for `TELEGRAM_SESSION_LIFETIME`.
    `ReqwestFetcher` (pyreqwest) is the alternative.
  - Requests for the same key are coalesced: in-process (`coalesce.py`), then
    across workers and servers through a Redis lease (`lease.py`).
  - `upstream.py` bounds the concurrency and the queue: beyond the queue,
    `503` with `Retry-After`.
- **Cache:** `TwoLevelCache` = native L1 (`MemoryCache`) in front of
  `RedisCache`.
  - Redis has a circuit breaker: it opens after 3 failures within 10 s, and
    the app keeps serving from L1 and t.me while it is open.
  - Every Redis key starts with `tme:v2:`:
    - `tme:v2:node:<host>:<pid>`: heartbeat and peer markers;
    - `tme:v2:rl:<window>:<client>`: the shared rate limit;
    - `tme:v2:feed:popularity` and `tme:v2:feed:refresher-leader`: the feed.
  - `CACHE_MODE` (`normal` by default) sets the page TTL (10 s) and the
    `Cache-Control` sent on responses that came through Cloudflare (the
    request has `CF-RAY`): `public, max-age=10, stale-while-revalidate=10`.
    Responses that set their own keep it:
    - static files and docs;
    - `/` and `/api`: 300 s;
    - `robots.txt` and the sitemaps: 3600 s;
    - rejections and `/healthz`: `no-store`.
- **Client IP in production:** Cloudflare → Caddy → app.
  - Caddy trusts only Cloudflare's IP ranges for `CF-Connecting-IP` and
    passes the client address on as `X-Forwarded-For`.
  - The app trusts that header only from the compose network
    `172.29.53.0/24`, which `deploy.yml` sets as `FORWARDED_ALLOW_IPS` when
    `PUBLIC_HOST` is set.
- **Rate limit:** GCRA per client IP in each process. With Redis, the
  processes add up a per-minute count, so the limit holds across all workers
  and servers.
  - Requests from the server itself are exempt: loopback *and* not forwarded
    (the image's `HEALTHCHECK`, the per-server smoke test).
  - A forwarded `X-Forwarded-For: 127.0.0.1` still counts.
  - Tests that need the limit must not use a loopback client:
    `tests/test_coalescing.py::call` uses `192.0.2.10` for that reason.
- **Settings:** every field of `settings.py`, env var = upper-case field name.
  Groups: server, API surface, features, protection, GraphQL limits,
  performance, upstream, cache/Redis, peers, feed, logging.
  - `docs/configuration.md` (×3 languages) lists them all, and a test
    enforces it; `.env.example` has the common ones.
  - `deploy/presets/{minimal,standard,full}.env` are the compose presets
    (`make up PRESET=…`).

## Layout

| Path | Contents |
|---|---|
| `src/telegram_me/__main__.py`, `server.py` | CLI (`serve`, `worker`, `version`), granian, ASGI factory |
| `src/telegram_me/app.py` | App factory, middleware order, lifespan |
| `src/telegram_me/container.py` | Builds the per-process services (cache, fetcher, peers, refresher) |
| `src/telegram_me/api/` | Routes, dependencies, errors, protection (`protection.py`), site (`site.py`), GraphQL (`graphql/`: schema, types, error codes, limits) |
| `src/telegram_me/services/` | Channels, feed, popularity, coalescing (`coalesce.py`, `lease.py`, `upstream.py`) |
| `src/telegram_me/telegram/` | HTTP client to t.me and thin wrappers over the native parser |
| `src/telegram_me/cache.py` | `MemoryCache` (native L1), `RedisCache` (circuit breaker), `TwoLevelCache` |
| `src/telegram_me/heartbeat.py`, `peers.py`, `memory.py` | Redis liveness markers, peer layer bridge, periodic `malloc_trim` |
| `src/telegram_me/settings.py` | Every setting; env var = upper-case field name |
| `src/telegram_me/static/`, `templates/` | Landing page (`landing.html`, `.css`, `.js`), the Swagger UI glue (`api.js`), proof of work in the browser (`pow.js`, `pow-worker.js`) |
| `rust/telegram-me-native/` | The native crate and its Python package (`python/telegram_me_native`, stubs in `_native.pyi`) |
| `tests/` | Contract snapshots, coalescing, memory guards, peers, unit and property tests; `integration/` (the image as two servers with Valkey) and `deploy/` (deploy scripts), both need Docker and `TME_TEST_IMAGE` |
| `tests/snapshots/` | `openapi.json`, `graphql/schema.graphql`, REST/GraphQL responses, `raw_digests.json` |
| `tests/fixtures/telegram/` | Recorded t.me pages (`make fixtures` re-records them) |
| `docs/`, `docs/es/`, `docs/ru/` | Documentation site in English, Spanish and Russian (Zensical; `zensical*.toml`) |
| `deploy/` | Production compose, Caddyfile, `remote-deploy.sh` (one server), `rollout.sh` (the fleet), `bootstrap-server.sh`, `presets/` |
| `scripts/` | Smoke matrix, API contract and coverage-floor checks, benchmarks, audits, fixture recording, docs build |

Local-only files, never committed:
- `.env.local` (gitignored; it holds `CF_BYPASS_KEY`);
- `site/` (the docs build output);
- `bestpractices-answers.md` (listed in `.git/info/exclude`).

## Commands

The toolchain is uv, Rust (stable) and Docker. `make help` lists every target.

```bash
make install        # uv sync + pre-commit hooks
make tools          # cargo-deny, cargo-machete; gitleaks, hadolint, trivy (Homebrew)
make native         # rebuild the Rust extension after editing rust/
make check          # what CI runs: make rust + lint + typecheck + test + audit
make rust           # cargo fmt --check, clippy pedantic (both feature sets), cargo test
make lint           # ruff, import-linter, vulture, typos, zizmor, hadolint, shellcheck, actionlint
make typecheck      # mypy --strict on src/ and tests/ (scripts/ is not covered)
make audit          # cargo-deny, cargo-machete, uv audit, gitleaks over the history
make image-scan     # trivy on IMAGE (default telegram-me:dev, from `make docker`)
make snapshots      # rewrite contract snapshots; review the diff!
make docs           # build en/es/ru (CI adds --strict)
make up && make smoke   # compose stack with Valkey + live smoke test
make contract       # breaking REST/GraphQL changes and lowered coverage floors vs origin/main
make docker && make integration deploy-test   # the image with real Valkey and deploy scripts
```

Before you open a PR, everything below must pass:

- `make check` (it needs uv 0.13.5 or later, for `uv audit`, and `make tools`)
- `make snapshots` with **no diff**
- the three strict docs builds:
  `uv run --only-group docs zensical build --clean --strict [-f zensical.es.toml | -f zensical.ru.toml]`
- `uv run pre-commit run --all-files`

The local venv is Python 3.13, but CI also tests 3.14. A warning that only
3.14 raises fails CI and not your local run. To check one test file on 3.14
without the app's conftest:

```bash
uv run --isolated --no-project --python 3.14 --with pytest python -X dev -m pytest -c /dev/null --noconftest -W error tests/unit/<file>.py
```

## Rules that are easy to break

- **The API contract is pinned.**
  - `tests/test_snapshots.py` and `tests/test_graphql_contract.py` compare every
    endpoint with `tests/snapshots/` over recorded t.me pages in
    `tests/fixtures/telegram/`. `raw_digests.json` pins REST responses byte for
    byte: key order, number formatting and escaping.
  - An intentional change means: regenerate the snapshots, review the diff,
    and add a CHANGELOG entry.
  - Never update snapshots to make a failing test pass without understanding
    why it failed. Example: GraphQL errors once came back in resolver
    completion order and made `graphql/missing.json` flaky. The fix was to
    sort them by query position (`api/graphql/errors.py`); the snapshot
    stayed as it was.
  - The **Guards** workflow (`scripts/api_contract.py`) fails a breaking
    change of `tests/snapshots/openapi.json` or
    `tests/snapshots/graphql/schema.graphql` against the base unless the PR
    is labelled `breaking-change`, which means a major version. Do not add
    the label yourself.
  - A change to the OpenAPI schema also means the maintainer must re-upload
    the schema to Cloudflare Schema Validation (see *Cloudflare* below).
    Mention it in the PR.
- **Coverage floors only go up:** `fail_under` in `pyproject.toml` (92.5),
  `fail-under-lines` in the crate's `Cargo.toml` (96). Never lower them.
- **The docs are in three languages.** `tests/unit/test_docs_config.py`
  fails when a setting is missing from any `configuration.md`, or when a
  translated page lacks a section of the English one. A new setting or docs
  section needs its Spanish and Russian versions in the same PR. Code, setting
  names, paths and JSON keys are never translated. Translated headings keep
  the English anchor: `## Título { #english-anchor }`.
- **Settings:** add the field in `settings.py`, then:
  - the docs (all three `configuration.md` files);
  - `.env.example`;
  - `deploy.yml` if production must set it (a repository variable
    `vars.X` passed as `VAR_X`, with a default);
  - CHANGELOG.
- **CHANGELOG:** Keep a Changelog format. New entries go under
  `## [Unreleased]` (`### Added` / `Changed` / `Fixed` / `Security`). Two open
  PRs both editing Unreleased conflict once one merges: resolve by keeping
  both entries.
- **Tests:**
  - Warnings are errors (`filterwarnings = ["error"]`), and asyncio is in
    auto mode.
  - A test stuck for 300 s dumps every thread's stack
    (`faulthandler_timeout`).
  - Use the `make_client` fixture and the fixture fetcher; tests must never
    reach t.me. Rust changes need `cargo test` coverage.
  - Close what you open: an `urllib` `HTTPError` must be closed
    (`with exc:`), or Python 3.14 raises `ResourceWarning`.
  - Scripts under `scripts/` are tested by loading them with `importlib`;
    see `tests/unit/test_api_contract.py` and `test_smoke_matrix.py`.
- **Secrets:** never hard-code keys or secrets, not even in tests; CodeQL
  fails the PR. Generate test keys at run time (`secrets.token_hex`,
  `getrandom`).
- **Style:** match the surrounding code. Python is typed for `mypy --strict`
  and linted with ruff's broad rule set. Rust is checked with clippy pedantic
  and `-D warnings`. Comments explain *why*, briefly.
- **Static checks** (all in `make check`; the list with settings is in
  `docs/contributing/testing.md#static-checks`):
  - Rust: no `unwrap()` outside tests, state the invariant with
    `expect("...")`. No `#[allow]`: use `#[expect(lint, reason = "...")]`.
    In `src/parse/` no indexing or slicing that can panic (`get`,
    `split_once`, `strip_prefix`): the release build aborts on panic, and
    takes the worker with it. `unsafe_code` is forbidden.
  - New crates must pass `deny.toml` (licenses, advisories, one version
    each). Bump `rust-version` with `RUST_VERSION`: an older value makes
    the resolver keep vulnerable releases.
  - Python layering is enforced by import-linter (`[tool.importlinter]`).
    A module imports only from layers below it: `__main__` → `server` →
    `app` → `api` → `container` → `services | heartbeat` → `peers` →
    `cache` → `telegram` → `schemas` → `validation | log | memory` →
    `settings`. Only `api` and `app` may import the web frameworks. Never add
    to `ignore_imports` to make it pass.
  - vulture checks `src/` alone: code that only tests use is dead. A name
    used by a framework goes in `tests/vulture_whitelist.py` with the
    reason.
  - Allows are narrow and explained (`# noqa: CODE - why`, an entry with a
    comment). No new per-file ignores just to pass. ruff flags an unused
    `noqa` (RUF100), so don't add one "just in case".
  - gitleaks runs on staged changes (pre-commit) and the whole history
    (CI). `.env.local` holds the production bypass key: never copy values
    from it into a tracked file, a test or a command in the docs.
- **Workflows:**
  - Actions are pinned by commit SHA with a version comment, and zizmor and
    actionlint lint every workflow.
  - Runners are pinned to `ubuntu-24.04` (`ubuntu-24.04-arm` for the arm64
    build), not `ubuntu-latest`: `ubuntu-latest` has been moving to Ubuntu 26
    since 2026-10-19.
  - The BoringSSL build-tools step skips apt when the image already has cmake
    and libclang.

## CI, branch protection and GitHub

- **Workflows:**
  - `ci.yml`: on PRs and pushes, and called by `release.yml`. Jobs: Rust;
    Lint & types; Tests on Python 3.13 and 3.14 (20 min timeout); then the
    Docker image smoke test, trivy, the integration tests (2 servers × 2
    workers with Valkey) and the deploy-scripts tests.
  - `guards.yml`: the API contract and coverage floors.
  - `release.yml`: on every push. Runs CI and Guards, builds amd64 and arm64
    natively, then pushes the multi-arch image to Docker Hub
    (`docker.io/koval01/telegram-me`) and GHCR. On `main` it calls
    `deploy.yml`; on a `v*` tag it creates the GitHub release.
  - `deploy.yml`: called by `release.yml`, or run by hand (Actions →
    Deploy, input `image_tag`, e.g. `sha-0123456789ab` for a rollback).
  - `docs.yml`: builds the docs and publishes them to GitHub Pages
    (`koval01.github.io/telegram-me`).
  - `nightly.yml` (03:23 UTC):
    - the image against live t.me;
    - production through the public URL;
    - 10 min of parser fuzzing;
    - long property and hostile-HTML runs.

    A failure opens or updates an issue labelled `nightly`.
  - `scorecard.yml`: OpenSSF Scorecard, with `publish_results: false`.
    OpenSSF Best Practices is project 15912.
  - `dependency-review.yml`.
- **`main` ruleset** (id 25823045):
  - Changes reach `main` only through a PR; no force-push, no deletion.
  - 10 required checks (Rust, Lint & types, Tests 3.13 and 3.14, Docker image
    smoke test, Integration, Deploy scripts, trivy, API contract and coverage
    floors, Dependency review), plus CodeQL with high-or-higher alerts.
  - Branches must be up to date: `gh pr update-branch <n>` (or merge
    `origin/main` into the branch) before merging. That reruns CI; the Rust
    job alone takes about 14 minutes.
  - Never rebase or force-push a PR branch.
- **Branches are not deleted on merge** (the repo setting is off). Use
  `gh pr merge <n> --merge --delete-branch` and delete stale local branches.
- **Merge commits**, not squash: `gh pr merge --merge`.
- **Reading CI logs:**
  - `gh run view --job <id> --log` works only after the whole run has
    finished. While it runs, use
    `gh api repos/koval01/telegram-me/check-runs/<job-id>/annotations`.
  - `gh api …/jobs/<id>/logs` refuses the colour codes unless you pass
    `--allow-escape-sequences`.
  - pytest prints progress without newlines, so the live log of a hung job
    shows only the last *finished* file.
- **Waiting on CI:** a background script that waits on
  `gh pr checks <n> --watch` stops if the network drops. Check the PR state
  again before concluding anything, and never act on a partial result.
- **The shell is zsh.** It does not split an unquoted `$var` into words: a
  loop over `"" "-f zensical.es.toml"` passes the flag as one argument. Use
  arrays or separate commands. `$name:t` and `$name:r` are zsh modifiers, so
  write `${name}` when a colon follows.

## Deployment: merging to `main` deploys production

- **Do not merge PRs into `main` unless the maintainer asks you to.** Open the
  PR and stop. An approval covers the PRs it names, not later ones.
- **The pipeline:** `release.yml` (CI + Guards → image `sha-<12 hex>`) →
  `deploy.yml` → `deploy/rollout.sh`, over SSH, one server at a time.
  1. Server 1 is the canary. `deploy/remote-deploy.sh` deploys it: health
     check, then the smoke matrix *inside* the container. It soaks under real
     traffic for `CANARY_SOAK` (60 s), then is verified again.
  2. The other servers follow the same way.
  3. With `PUBLIC_URL`/`PUBLIC_HOST`, the smoke matrix runs through
     Cloudflare with the bypass key and `--edge-blocks-invalid`.
  4. Any failure after a server took the release rolls back every server
     that took it (`remote-deploy.sh --rollback`, using `.previous-image`).
- **Production:**
  - two small VMs (Ubuntu 26.04 minimal), app dir `~/telegram-me`;
  - compose project `telegram-me`: `api` plus the `proxy` profile (Caddy with
    a Cloudflare Origin CA certificate), and optionally the `worker` profile;
  - a shared Redis (`REDIS_URL`);
  - containers are read-only, run as UID 10001 and drop all capabilities.
- **Repository variables** (production config, all read in `deploy.yml`):
  - `PUBLIC_HOST=tme.koval-dev.org`;
  - `PUBLIC_HEALTHCHECK_URL`;
  - `ENABLE_RATE_LIMIT=true`;
  - `RATE_LIMIT_BURST=30`;
  - optional, with defaults when unset: `ENABLE_FEED` (true),
    `ENABLE_PREVIEWS` (true), `RATE_LIMIT_RPM` (120), `ENABLE_POW`,
    `ENABLE_PEERS`, `CANARY_SOAK`, `DEPLOY_IMAGE`, `FORWARDED_ALLOW_IPS` and
    others.
  - A changed variable takes effect with the next deploy, so set it before
    the merge that should carry it.
- **Secrets** (names only): `SSH_KEY`, `SSH_KNOWN_HOSTS`, `SERVERS_IPS`
  (masked in logs), `REDIS_URL`, `TLS_CERT`, `TLS_KEY`, `CF_BYPASS_KEY`,
  `DOCKER_USERNAME`, `DOCKER_PASSWORD`, `DOCKERHUB_TOKEN`; `POW_SECRET` and
  `PEER_SECRET` once those features are on.
- **Checking production:**
  - Every request needs `x-bypass-key` (from `.env.local`, `CF_BYPASS_KEY`).
    Without it Cloudflare answers `403`, which says nothing about the app.
  - Version: `info.version` in `/openapi.json`.
  - Rate limit: `x-ratelimit-limit` / `x-ratelimit-remaining`; with burst 30
    the first request shows 29.
  - Node: `x-app-node`.
  - Full check:
    `python3 scripts/smoke_matrix.py --base-url https://tme.koval-dev.org --preset auto --edge-blocks-invalid --header "x-bypass-key:$CF_BYPASS_KEY"`.

## Cloudflare in front of production

The maintainer manages Cloudflare; you have no access to it. Things that
explain otherwise puzzling results:

- **Bot protection** blocks anything without the `x-bypass-key` skip-rule
  header (`curl` included). That is intended.
- **Schema Validation (Block)** uses an OpenAPI **3.0.3** conversion of
  `/openapi.json`.
  - Cloudflare rejects FastAPI's 3.1: numeric `exclusiveMinimum` must become
    `minimum` plus `exclusiveMinimum: true`, `anyOf: [..., {type: null}]`
    must become `nullable: true`, and the file needs
    `servers: [{url: https://tme.koval-dev.org}]`.
  - Requests that don't match the schema get `403`, error **1020**, at the
    edge, *even with the bypass key*. A JSON client gets a problem document
    (`cloudflare_error: true`, `error_code: 1020`); others get the text
    `error code: 1020`.
  - That is why every smoke run through the public URL uses
    `--edge-blocks-invalid`: invalid requests pass on `422` or that block,
    and any other `403` fails. `tests/unit/test_smoke_matrix.py` checks that
    `rollout.sh` and `nightly.yml` pass the flag.
- **Caching:**
  - Cloudflare keys its cache on the full query string, so `?x=1` misses the
    edge. The app's own cache ignores unknown parameters, so t.me never
    sees that traffic.
  - The only query parameters the API reads are `position` (REST) and
    `query`, `variables`, `operationName` (`GET /graphql`).
  - Recommended cache rule: Edge TTL "use cache-control header if present,
    bypass cache if not".
- **Speed Brain** answers prefetches it cannot serve from its cache with
  `503` (`Cf-Speculation-Refused`). That comes from Cloudflare, not the app.

## Releasing

- **Versions:** semver, but the maintainer wants **patch releases** (2.2.x)
  for fixes, hardening, CI and small behaviour changes. Bump the minor only
  when the maintainer decides; the major only for a `breaking-change`.
- **Flow** (also in `docs/contributing/releasing.md`):
  1. Open a release PR `Release X.Y.Z`:
     - `version` in `pyproject.toml`, then `uv lock`;
     - Unreleased moves into `## [X.Y.Z] - YYYY-MM-DD` with a one-paragraph
       summary;
     - the compare links at the bottom of `CHANGELOG.md`:
       `[Unreleased]` points at `vX.Y.Z...HEAD`, and a new `[X.Y.Z]` points
       at the previous tag.
  2. Merge it. That deploys `sha-…` to production.
  3. Verify that production reports the version.
  4. Only when the release run of the merge commit **on `main` succeeded**,
     tag it: `git tag -a vX.Y.Z -m "Release X.Y.Z"` and
     `git push origin vX.Y.Z`. (Once, 2.2.0 was tagged while its main run
     had failed; don't let a script do that.)
  5. The tag run publishes the GitHub release from the CHANGELOG section and
     the image tags `X.Y.Z`, `X.Y` and `X` (amd64 and arm64). Check it with
     `gh release view vX.Y.Z` and `docker buildx imagetools inspect`.
- **Never delete releases or tags** without the maintainer's explicit
  request. Old `Auto:` draft releases exist; the maintainer deletes them.

## Commits and PRs

- Work on a branch (`fix/…`, `feat/…`, `ci/…`, `docs/…`,
  `release/vX.Y.Z`), never on `main`.
- Imperative, descriptive commit subjects. The body explains why, with numbers
  for performance work.
- PR descriptions state what changed, why, the measurements, and which checks
  ran. Say plainly what was not verified (for example, the Docker integration
  tests are left to CI).
- Reference issues with `Fixes #N` so the merge closes them.
