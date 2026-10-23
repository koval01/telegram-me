# Desarrollo local

## Requisitos { #requirements }

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 o
  posterior. uv instala Python 3.13+ automáticamente si falta.
- [Rust](https://rustup.rs/) 1.85 o posterior (la CI y la imagen usan 1.99):
  `uv sync` compila la extensión nativa `telegram-me-native`.
- Opcional: Docker, para ejecutar la imagen o el stack de compose.
- Opcional: GNU `make` para los atajos de abajo.

## Instalación { #setup }

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
make install        # uv sync + pre-commit hooks
```

Sin `make`: `uv sync && uv run pre-commit install`.

## Ejecutar la API { #run-the-api }

```bash
make dev            # uv run telegram-me serve --reload
```

El servidor escucha en <http://localhost:3000> y se reinicia cuando cambia el
código. La configuración se lee de las variables de entorno y de `.env` /
`.env.local` en el directorio de trabajo, por ejemplo:

```dotenv title=".env"
ENABLE_FEED=true
LOG_LEVEL=DEBUG
```

Sin `REDIS_URL` se usa la caché en memoria. Para desarrollar contra Redis,
arranca uno (`docker run -p 6379:6379 valkey/valkey:9.1-alpine`) y define
`REDIS_URL=redis://localhost:6379/0`.

## Comandos habituales { #everyday-commands }

| Comando | Qué hace |
|---|---|
| `make test` | Batería completa de pruebas con cobertura (sin red, ~2 s) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make format` | Formatea y corrige automáticamente |
| `make typecheck` | `mypy` en modo estricto |
| `make rust` | `cargo fmt --check`, clippy (pedantic) y `cargo test` |
| `make native` | Recompila la extensión de Rust después de editar `rust/` |
| `make check` | Todo lo que ejecuta la CI antes de construir la imagen |
| `make snapshots` | Regenera los snapshots del contrato de la API tras un cambio intencionado |
| `make fixtures` | Vuelve a grabar las páginas de t.me que usan las pruebas |
| `make smoke` | Prueba de humo de una instancia en marcha contra t.me real |
| `make docker` / `make up` | Construye la imagen / arranca el stack de compose |
| `make docs-serve` | Vista previa de esta documentación en <http://localhost:8001> (`DOCS_LANG=es` para la versión en español) |

## Estructura del proyecto { #project-layout }

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
└── telegram/         # t.me client and wrappers of the Rust page parser
rust/telegram-me-native/
├── src/              # rank.rs, simhash.rs, topics.rs, text.rs, python.rs (PyO3)
└── python/           # telegram_me_native package + type stubs
tests/
├── fixtures/telegram # recorded t.me responses (gzip) + manifest.json
├── snapshots/        # pinned JSON of every endpoint
├── test_snapshots.py # contract tests
└── unit/             # focused unit tests
```

Continúa con [Pruebas](../contributing/testing.md) antes de tu primer cambio.
