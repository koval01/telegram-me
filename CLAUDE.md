# CLAUDE.md

Guidance for Claude Code and other AI coding agents working in this repository.
Humans: see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[documentation](https://koval01.github.io/telegram-me/).

## What this is

TelegramMe turns public Telegram channel pages (`t.me/s/<channel>`) into JSON.
It serves REST `/v1` and GraphQL `/graphql`. Demo: https://tme.koval-dev.org
(behind Cloudflare bot protection; it is not a free public instance).

## Architecture rule: heavy work is native

- Anything on the hot path or CPU/memory heavy belongs in Rust, in the
  `rust/telegram-me-native` crate (PyO3, maturin, abi3 for Python 3.13+).
  Native code we write is **Rust only**. Ready-made native components may be
  C, C++ or Rust: pyreqwest, granian, pydantic-core, hiredis, zstd.
- Python (`src/telegram_me`) is glue: wiring, business rules, orchestration
  and scripts. If a change adds per-request Python work, consider the crate
  first.
- The crate already holds:
  - the t.me page parser (`parse/`);
  - feed building and ranking (`feed.rs`, `rank.rs`);
  - the in-process L1 cache (`cache.rs`, moka plus zstd);
  - the rate limiter (`ratelimit.rs`, GCRA) and proof of work (`pow.rs`);
  - the request-scheduling core (`sched.rs`).
- `docs/internals/rust-roadmap.md` plans what moves next.
- **The GIL.** Release it (`py.detach`) only around work long enough to be
  worth it. Short calls made from the event loop keep it: see the comments in
  `src/python.rs` for the measurements behind that.

## Layout

| Path | Contents |
|---|---|
| `src/telegram_me/app.py` | App factory, middleware order, lifespan |
| `src/telegram_me/api/` | Routes, dependencies, errors, GraphQL (`graphql/`), protection middleware |
| `src/telegram_me/services/` | Channels, feed, popularity, coalescing (`coalesce.py`, `lease.py`, `upstream.py`) |
| `src/telegram_me/telegram/` | HTTP client to t.me and thin wrappers over the native parser |
| `src/telegram_me/cache.py` | `MemoryCache` (native L1), `RedisCache` (circuit breaker), `TwoLevelCache` |
| `src/telegram_me/settings.py` | Every setting; env var = upper-case field name |
| `rust/telegram-me-native/` | The native crate and its Python package (`python/telegram_me_native`, stubs in `_native.pyi`) |
| `tests/` | Contract snapshots, coalescing, memory guards, unit and property tests; `integration/` (the image as two servers with Valkey) and `deploy/` (deploy scripts), both need Docker and `TME_TEST_IMAGE` |
| `docs/`, `docs/es/`, `docs/ru/` | Documentation site in English, Spanish and Russian (Zensical) |
| `deploy/` | Production compose, Caddyfile, `remote-deploy.sh` (one server), `rollout.sh` (the fleet), `bootstrap-server.sh` |
| `scripts/` | Smoke matrix, API contract and coverage-floor checks, benchmarks, audits, fixture recording |

## Commands

The toolchain is uv, Rust (stable) and Docker. `make help` lists every target.

```bash
make install        # uv sync + pre-commit hooks
make native         # rebuild the Rust extension after editing rust/
make check          # what CI runs: make rust + lint + typecheck + test
make rust           # cargo fmt --check, clippy pedantic (both feature sets), cargo test
make snapshots      # rewrite contract snapshots; review the diff!
make docs           # build en/es/ru (CI adds --strict)
make up && make smoke   # compose stack with Valkey + live smoke test
make contract       # breaking REST/GraphQL changes and lowered coverage floors vs origin/main
make docker && make integration deploy-test   # the image with real Valkey and deploy scripts
```

Before you open a PR, everything below must pass:

- `make rust`
- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q`
- `make snapshots` with **no diff**
- the three strict docs builds:
  `uv run --only-group docs zensical build --clean --strict [-f zensical.es.toml | -f zensical.ru.toml]`
- `uv run pre-commit run --all-files`

## Rules that are easy to break

- **The API contract is pinned.**
  - `tests/test_snapshots.py` and `tests/test_graphql_contract.py` compare every
    endpoint with `tests/snapshots/` over recorded t.me pages in
    `tests/fixtures/telegram/`. `raw_digests.json` pins REST responses byte for
    byte: key order, number formatting and escaping.
  - An intentional change means: regenerate the snapshots, review the diff,
    and add a CHANGELOG entry.
  - Never update snapshots to make a failing test pass without understanding
    why it failed.
  - The **Guards** workflow (`scripts/api_contract.py`) fails a breaking
    change of `tests/snapshots/openapi.json` or `schema.graphql` against the
    base unless the PR is labelled `breaking-change`, which means a major
    version. Do not add the label yourself.
- **Coverage floors only go up:** `fail_under` in `pyproject.toml`,
  `fail-under-lines` in the crate's `Cargo.toml`. Never lower them.
- **The docs are in three languages.** `tests/unit/test_docs_config.py`
  fails when a setting is missing from any `configuration.md`, or when a
  translated page lacks a section of the English one. A new setting or docs
  section needs its Spanish and Russian versions in the same PR. Code, setting
  names, paths and JSON keys are never translated. Translated headings keep
  the English anchor: `## Título { #english-anchor }`.
- **Settings:** add the field in `settings.py`, then:
  - the docs (all three `configuration.md` files);
  - `.env.example`;
  - `deploy.yml` if production must set it;
  - CHANGELOG.
- **CHANGELOG:** Keep a Changelog format. New entries go under
  `## [Unreleased]`.
- **Tests:** warnings are errors, and asyncio is in auto mode. Use the
  `make_client` fixture and the fixture fetcher; tests must never reach
  t.me. Rust changes need `cargo test` coverage.
- **Secrets:** never hard-code keys or secrets, not even in tests; CodeQL
  fails the PR. Generate test keys at run time (`secrets.token_hex`,
  `getrandom`).
- **Style:** match the surrounding code. Python is typed for `mypy --strict`
  and linted with ruff's broad rule set. Rust is checked with clippy pedantic
  and `-D warnings`. Comments explain *why*, briefly.

## Deployment: merging to `main` deploys production

- `.github/workflows/release.yml` runs CI and Guards, builds and pushes the
  multi-arch image (Docker Hub and GHCR), then `deploy.yml` rolls it out to
  every server over SSH, one server at a time (`deploy/rollout.sh`).
  `deploy/remote-deploy.sh` rolls a server back when it is unhealthy or fails
  the smoke test; server 1 is a canary; a later failure rolls back every
  server that took the release.
- **Do not merge PRs into `main` unless the maintainer asks you to.** Open the
  PR and stop.
- Pushing a `v*` tag publishes semver image tags and a GitHub release built
  from the matching CHANGELOG section.
- Production (`tme.koval-dev.org`): two small VMs behind Cloudflare, with Caddy
  on each (`PUBLIC_HOST`) and a shared Redis. Every request to it needs the
  Cloudflare skip-rule header `x-bypass-key`. The key is kept outside the repo
  (`CF_BYPASS_KEY`); without it you get `403` from Cloudflare, which says
  nothing about the app's health.

## Commits and PRs

- Imperative, descriptive commit subjects. The body explains why, with numbers
  for performance work.
- PR descriptions state what changed, why, the measurements, and which checks
  ran. Say plainly what was not verified.
- The license is Apache-2.0. `NOTICE` carries the disclaimer that the project
  is not affiliated with Telegram.
