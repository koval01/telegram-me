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
- Слои: `api` → `services` → `telegram` / `cache`; ничто ниже `api` не
  импортирует FastAPI. Новые долгоживущие объекты подключайте в
  `container.py`.
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
Python, GitHub Actions и базовых образов Docker.

## Pre-commit { #pre-commit }

`make install` устанавливает git-хуки, которые при каждом коммите запускают
ruff (с автоисправлением), ruff format, mypy и несколько гигиенических
проверок с версиями из `uv.lock`. Запустить их вручную можно командой
`uv run pre-commit run --all-files`.

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
