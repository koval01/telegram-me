# Разработка

Подготовьте проект, как описано в разделе
[Локальная разработка](../getting-started/local-development.md), а затем:

## Рабочий процесс { #workflow }

1. Создайте ветку от `main`.
2. Внесите изменение вместе с тестами.
3. `make check` — линтер, типы и тесты, ровно то, что CI запускает первым.
4. Если вы намеренно изменили то, что возвращает эндпоинт, запустите
   `make snapshots` и просмотрите `git diff tests/snapshots` (см.
   [Тестирование](testing.md)).
5. Для изменений, заметных пользователям, добавьте запись в раздел
   **Unreleased** файла `CHANGELOG.md`.
6. Откройте pull request. CI запускает линтер, mypy, тесты на Python 3.13 и
   3.14, собирает образ и прогоняет smoke-тест на живом t.me.

## Стиль кода { #code-style }

- Форматирование и линтинг: **ruff** (длина строки 100, широкий набор правил,
  включая bugbear, pyupgrade, bandit и pydocstyle с соглашением Google).
  `make format` исправляет большую часть замечаний.
- Типы: **mypy --strict** для `src` и `tests` с плагином Pydantic. Публичные
  функции полностью аннотированы; `Any` используется только для сырого JSON.
- Docstrings: у каждого публичного модуля, класса и функции, в стиле Google.
- Слои: `api` → `services` → `cache` → `telegram`; ничто ниже `api` не
  импортирует FastAPI. Оба правила проверяет import-linter
  (`[tool.importlinter]` в `pyproject.toml`). Новые долгоживущие объекты
  подключайте в `container.py`.
- Rust: никаких `unwrap()` в рабочем коде (вместо этого `expect("...")`
  называет инвариант), а в `src/parse/` никакой индексации и срезов, которые
  могут вызвать панику: используйте `get`, `split_once`, `strip_prefix`.
  Расширение при панике аварийно завершается, и вместе с ним падает worker.
  Все инструменты описаны в разделе
  [Статические проверки](testing.md#static-checks).
- Ошибки: выбрасывайте доменные ошибки из `telegram/errors.py`; API
  сопоставляет их с кодами статуса в одном месте (`api/errors.py`).
- Сохраняйте стабильность контракта `/v1`. Новые поля — можно; удаление или
  переименование полей либо изменение их типов требует новой версии API.

## Зависимости { #dependencies }

Зависимости объявлены в `pyproject.toml` и зафиксированы в `uv.lock`:

```bash
uv add <package>             # runtime dependency
uv add --dev <package>       # development tool
uv lock --upgrade            # refresh everything to the newest versions
```

Dependabot раз в неделю открывает сгруппированные обновления для пакетов
Python, GitHub Actions и базовых образов Docker, для версий не моложе недели
(обновления безопасности приходят сразу).

## Pre-commit { #pre-commit }

`make install` устанавливает git-хуки, которые запускаются при каждом
коммите: ruff (с автоисправлением), ruff format, mypy, import-linter,
vulture, typos, zizmor, gitleaks по подготовленным изменениям, cargo fmt и
clippy, а при изменении их файлов ещё hadolint, cargo-deny и cargo-machete.
Инструменты Python берутся из `uv.lock`; остальные ставит `make tools`.
Запустить их вручную можно командой `uv run pre-commit run --all-files`.

## Документация { #documentation }

Сайт пишется на английском в `docs/`, а переводы на испанский и русский
лежат в `docs/es/` и `docs/ru/` и повторяют его структуру файлов. Каждый язык
собирается отдельно: `zensical.toml` собирает английскую версию в `site/`, а
`zensical.es.toml` / `zensical.ru.toml` — переводы в `site/es/` и `site/ru/`.
Переключатель языка в шапке оставляет читателя на той же странице и в том
же разделе (`overrides/main.html`).

```bash
make docs-serve                  # live preview of English on :8001
make docs-serve DOCS_LANG=ru     # live preview of the Russian pages
make docs-preview                # build all languages and serve site/ with the switcher
```

Меняя страницу, обновите её переводы в том же pull request или создайте для
них issue. Переведённые заголовки сохраняют английский якорь через явный id
(`## Пример { #example }`), поэтому ссылки и переключатель ведут к одному и
тому же разделу на всех языках. Код, команды, названия настроек и ключи JSON
не переводятся. `tests/unit/test_docs_config.py` проверяет, что у каждой
страницы есть перевод и что три конфигурационных файла согласованы.
