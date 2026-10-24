# Локальная разработка

## Требования { #requirements }

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 или
  новее. Если Python 3.13+ не установлен, uv установит его автоматически.
- [Rust](https://rustup.rs/) 1.99 или новее (эту версию используют CI и образ):
  `uv sync` компилирует нативное расширение `telegram-me-native`.
- Необязательно: Docker — чтобы запускать образ или compose-стек.
- Необязательно: GNU `make` — для сокращённых команд ниже.

## Установка { #setup }

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
make install        # uv sync + pre-commit hooks
```

Без `make`: `uv sync && uv run pre-commit install`.

## Запуск API { #run-the-api }

```bash
make dev            # uv run telegram-me serve --reload
```

Сервер слушает <http://localhost:3000> и перезапускается при изменении кода.
Конфигурация читается из переменных окружения и из `.env` / `.env.local` в
рабочем каталоге, например:

```dotenv title=".env"
ENABLE_FEED=true
LOG_LEVEL=DEBUG
```

Без `REDIS_URL` используется кэш в памяти. Чтобы разрабатывать с Redis,
запустите его (`docker run -p 6379:6379 valkey/valkey:9.1-alpine`) и задайте
`REDIS_URL=redis://localhost:6379/0`.

## Повседневные команды { #everyday-commands }

| Команда | Что делает |
|---|---|
| `make test` | Полный набор тестов с покрытием (без сети, ~2 с) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make format` | Форматирование и автоисправление |
| `make typecheck` | `mypy` в строгом режиме |
| `make rust` | `cargo fmt --check`, clippy (pedantic) и `cargo test` |
| `make native` | Пересобрать расширение на Rust после правок в `rust/` |
| `make check` | Всё, что CI запускает перед сборкой образа |
| `make snapshots` | Перегенерировать снапшоты контракта API после намеренного изменения |
| `make fixtures` | Заново записать страницы t.me, которые используют тесты |
| `make smoke` | Smoke-тест запущенного экземпляра на живом t.me |
| `make docker` / `make up` | Собрать образ / запустить compose-стек |
| `make docs-serve` | Предпросмотр этой документации на <http://localhost:8001> (`DOCS_LANG=ru` — русская версия) |

## Структура проекта { #project-layout }

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

Перед первым изменением прочитайте раздел [Тестирование](../contributing/testing.md).
