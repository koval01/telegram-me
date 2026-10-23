# Local development

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 or newer.
  uv installs Python 3.13+ automatically when it is missing.
- [Rust](https://rustup.rs/) 1.85 or newer (CI and the image use 1.99): `uv sync`
  compiles the native extension `telegram-me-native`.
- Optional: Docker, to run the image or the compose stack.
- Optional: GNU `make` for the shortcuts below.

## Setup

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
make install        # uv sync + pre-commit hooks
```

Without `make`: `uv sync && uv run pre-commit install`.

## Run the API

```bash
make dev            # uv run telegram-me serve --reload
```

The server listens on <http://localhost:3000> and restarts on code changes.
Configuration is read from environment variables and from `.env` /
`.env.local` in the working directory, for example:

```dotenv title=".env"
ENABLE_FEED=true
LOG_LEVEL=DEBUG
```

Without `REDIS_URL` the in-memory cache is used. To develop against Redis,
start one (`docker run -p 6379:6379 valkey/valkey:9.1-alpine`) and set
`REDIS_URL=redis://localhost:6379/0`.

## Everyday commands

| Command | What it does |
|---|---|
| `make test` | Full test suite with coverage (offline, ~2 s) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make format` | Format and autofix |
| `make typecheck` | `mypy` in strict mode |
| `make rust` | `cargo fmt --check`, clippy (pedantic) and `cargo test` |
| `make native` | Rebuild the Rust extension after editing `rust/` |
| `make check` | Everything CI runs before building the image |
| `make snapshots` | Regenerate API contract snapshots after an intended change |
| `make fixtures` | Re-record t.me pages used by the tests |
| `make smoke` | Smoke-test a running instance against live t.me |
| `make docker` / `make up` | Build the image / start the compose stack |
| `make docs-serve` | Preview this documentation on <http://localhost:8001> |

## Project layout

```text
src/telegram_me/
├── app.py            # create_app(): FastAPI app, lifespan, middleware
├── server.py         # granian server and standalone feed worker
├── __main__.py       # `telegram-me serve | worker | version`
├── settings.py       # every configuration option
├── container.py      # wiring of long-lived objects
├── cache.py          # Redis / in-memory cache with circuit breaker
├── api/              # routes, dependencies, middleware, error mapping
├── schemas/          # Pydantic response and request models
├── services/         # channel pages, feed, scoring, popularity, coalescing
└── telegram/         # t.me client and lexbor-based HTML parsers
rust/telegram-me-native/
├── src/              # rank.rs, simhash.rs, topics.rs, text.rs, python.rs (PyO3)
└── python/           # telegram_me_native package + type stubs
tests/
├── fixtures/telegram # recorded t.me responses (gzip) + manifest.json
├── snapshots/        # pinned JSON of every endpoint
├── test_snapshots.py # contract tests
└── unit/             # focused unit tests
```

Continue with [Testing](../contributing/testing.md) before your first change.
