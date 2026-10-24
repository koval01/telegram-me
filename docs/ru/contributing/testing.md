# Тестирование

```bash
make test          # ~550 tests, about 30 seconds, no network
make rust          # Rust: fmt, clippy, tests (parsers on hostile HTML included)
make integration   # the built image as two servers with Valkey (Docker)
```

Набор тестов по умолчанию никогда не обращается к t.me. Каждый merge в `main`
разворачивается в продакшен, поэтому именно эти тесты и
[проверки CI](#ci-jobs) отделяют сломанный коммит от пользователей.

## Снапшоты контракта { #contract-snapshots }

`tests/test_snapshots.py` вызывает каждый эндпоинт на **записанных страницах
t.me** и сравнивает полный JSON-ответ с файлом в `tests/snapshots/`. Любое
изменение того, что возвращает API, — переименованное поле, другое число,
пропавший пост — роняет тест и видно как читаемый diff. JSON-файлы
сравниваются в разобранном виде, поэтому точные байты каждого ответа тоже
зафиксированы — их SHA-256 в `tests/snapshots/raw_digests.json`: порядок
ключей, формат чисел и экранирование строк тоже не должны меняться, и именно
это позволяет переносить код с pydantic на Rust, не трогая контракт.

Записанные страницы хранятся в `tests/fixtures/telegram/` (gzip) вместе с
`manifest.json`, где описано, на какой запрос отвечает каждый файл.
`FixtureFetcher` подменяет сетевой слой и роняет тест при любом запросе,
который не был записан. Время замораживается с помощью `time-machine`, потому
что оценка в ленте зависит от «сейчас».

Покрыты шесть каналов (`durov`, `telegram`, `stranaua`, `tginfo`,
`nebudetafk`, `nebudetgg`), оба режима парсера, пагинация в обе стороны,
превью, несуществующий канал, лента и отдельные посты с inline-кнопкой,
опросом, стикером, ответом, превью ссылки и голосовым сообщением.
Синтетическая лента, записанная прямо в кэш, добавляет свёрнутые дубликаты и
страницы, которые парсер никогда бы не выдал (без username, без информации о
канале, посты без footer или content).

`tests/test_graphql_contract.py` делает то же самое для GraphQL: полная схема
(SDL) в `tests/snapshots/graphql/schema.graphql`, снапшот на каждый запрос и
проверки того, что каждый результат GraphQL совпадает с соответствующим
REST-ответом. `tests/test_openapi_contract.py` фиксирует REST-контракт целиком
— документ OpenAPI со всеми включёнными функциями — в
`tests/snapshots/openapi.json`.

### Намеренное изменение вывода { #changing-the-output-on-purpose }

```bash
make snapshots                 # UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py tests/test_graphql_contract.py tests/test_openapi_contract.py
git diff --stat tests/snapshots
git diff tests/snapshots        # review every change
```

### Несовместимые изменения { #breaking-changes }

Снапшот лишь показывает, что контракт изменился. `scripts/api_contract.py`
решает, ломает ли изменение клиентов: сравнивает `openapi.json` и
`schema.graphql` с базой pull request, то есть с тем, что отдаёт продакшен:

```bash
make contract                  # against origin/main
```

Несовместимо: удалённые операция, поле или значение enum, поле ответа,
которое теперь может отсутствовать или иметь другой тип (включая `null`), и
всё, что отклоняет запросы, принимавшиеся раньше (новый обязательный параметр
или поле, более узкий тип, меньший лимит). Добавления совместимы. Схему
GraphQL сравнивает graphql-core (`find_breaking_changes`). Workflow **Guards**
роняет pull request с несовместимым изменением, если у него нет метки
`breaking-change`, которая означает мажорную версию (см.
[Выпуск релизов](releasing.md)).

### Запись новых страниц { #recording-new-pages }

```bash
make fixtures                          # re-record everything (changes many snapshots)
make fixtures CHANNELS="nebudetgg"     # add or refresh specific channels
```

Запись использует продакшен-стек сетевого клиента. Повторная запись меняет
содержимое (просмотры, новые посты), поэтому после неё перегенерируйте и
просмотрите снапшоты. Чтобы покрыть редкий тип поста, добавьте его ID в
`EXTRA_POSTS` в `scripts/record_fixtures.py`.

## Модульные тесты { #unit-tests }

`tests/unit/` проверяет компоненты по отдельности:

| Файл | Что проверяет |
|---|---|
| `test_client.py` | Построение URL, сопоставление статусов, валидацию, единственный сетевой повтор |
| `test_fetcher.py` | `ReqwestFetcher` против настоящего локального HTTP-сервера: gzip, заголовки, пустые тела POST |
| `test_cache.py` | Кэш внутри процесса (лимит, сжатие, истечение срока), бэкенд Redis, circuit breaker, двухуровневые чтения, заполнение, сбой |
| `test_memory_trim.py` | Периодический `malloc_trim` (только glibc), его отключение |
| `test_coalesce.py` | Общий результат single-flight, распространение ошибок, безопасность при отмене |
| `test_popularity.py` | Затухание популярности и выбор лидера в Redis (fakeredis с Lua) |
| `test_services.py` | Кэши страниц и ответов, превью, лента со stale-while-revalidate, расписание рефрешера |
| `test_parsing.py` | Нативный парсер страниц глазами Python: сущности, медиа, пересылки, изоляция битых постов |
| `test_feed_ranking.py` | Сигналы ленты и нативный ранжировщик: относительная вовлечённость, свежесть, дубликаты, темы, разнообразие |
| `test_graphql.py` | Форма схемы GraphQL, резолверы, ошибки |
| `test_graphql_limits.py` | Лимиты GraphQL на глубину, стоимость, алиасы, токены и пакеты |
| `test_api.py` | Валидацию, feature flags, заголовки, ограничение частоты запросов, CORS, документацию |
| `test_runtime.py` | Жизненный цикл встроенного рефрешера, JSON-логи |
| `test_docs_config.py` | Переводы документации: наличие всех страниц, согласованность конфигурации с английской |
| `test_api_contract.py` | Какие изменения контракта `scripts/api_contract.py` считает несовместимыми |
| `test_properties.py` | Тесты на основе свойств, см. [ниже](#property-and-fuzz-tests) |

Тесты поддерживают async (`pytest-asyncio` в режиме auto), а предупреждения
считаются ошибками.

## Тесты свойств и фаззинг { #property-and-fuzz-tests }

Каждая страница t.me — это вход, который сервис не контролирует, а нативное
расширение собрано с `panic = "abort"`: паника в парсере роняет весь
процесс-воркер. Сгенерированные входные данные ищут то, что пропускают
написанные вручную случаи.

- `tests/unit/test_properties.py` (hypothesis): какую бы страницу ни вернул
  t.me (записанные страницы с удалёнными, повторёнными, обрезанными или
  вставленными частями), каждый эндпоинт отвечает 200, 404 или 502, но не 500;
  обёртки парсера падают только с `TelegramError`; middleware защиты отвечает
  на любой заголовок `X-PoW`, путь и метод корректно сформированным ответом,
  принимает правильное доказательство ровно один раз и точно считает лимит
  запросов; лента содержит каждый пост один раз, только из запрошенных
  каналов, в любом их порядке. Примеры берутся из фиксированного seed;
  `HYPOTHESIS_PROFILE=nightly` ищет дольше со случайными.
- `rust/telegram-me-native/tests/hostile_html.rs` (`cargo test`): каждая
  записанная страница целиком и с мутациями из фиксированного seed, мешанина
  из разметки t.me и абсурдных значений, глубоко вложенная разметка в потоке
  со стеком 1 MiB и время разбора, которое должно расти линейно (вход в 4
  раза больше — меньше чем в 9 раз дольше) для множества постов, сущностей,
  незакрытых тегов и шаблонов с look-ahead. `HOSTILE_CASES` и `HOSTILE_SEED`
  запускают его дольше или иначе.
- `rust/telegram-me-native/fuzz/` (cargo-fuzz, nightly Rust): фаззер с
  обратной связью по покрытию для всех парсеров страниц, засеянный
  записанными страницами:

    ```bash
    python3 scripts/fuzz_corpus.py rust/telegram-me-native/fuzz/corpus/pages
    cd rust/telegram-me-native/fuzz && cargo +nightly fuzz run -s none pages corpus/pages -- -max_total_time=60
    ```

## Память { #memory }

`tests/test_memory.py` (маркер `memory`) отправляет тысячи запросов напрямую в
ASGI-приложение по алиасам записанных каналов (`tests/workload.py`) и падает,
если аллокации Python или, в Linux, резидентная память продолжают расти после
прогрева, или если кэш внутри процесса превышает свой лимит. Он входит в
запуск по умолчанию, поэтому CI его выполняет; локально его можно пропустить
через `-m "not memory"`. Записи кэша живут в куче Rust, которую `tracemalloc`
не видит: что лимит учитывает то, что они реально занимают, проверяет
`rust/telegram-me-native/tests/cache_memory.rs` со считающим аллокатором.

`scripts/memory_profile.py` выводит стоящие за этим цифры: стоимость импорта
главного процесса и воркера, размер одной закэшированной страницы, а также RSS
и аллокации Python, замеренные во время длинной нагрузки:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 50
```

RSS читается из `/proc`, поэтому для осмысленных цифр запускайте его в Linux.

## Интеграционные тесты { #integration-tests }

`tests/integration/` запускает собранный образ так же, как в продакшене, —
то, что тесты выше могут лишь имитировать через fakeredis или внутри одного
процесса: `tests/integration/compose.yaml` поднимает два сервера по два
воркера granian, которые делят настоящий Valkey, перед поддельным t.me
(`tests/integration/fake_telegram.py` отдаёт записанные страницы и считает
запросы; серверы обращаются к нему через
[`TELEGRAM_BASE_URL`](../configuration.md#telegram_base_url)).

| Тест | Показывает |
|---|---|
| `test_second_server_reads_the_first_servers_cache` | двухуровневый кэш: страницу, загруженную одним сервером, другой отдаёт из Redis |
| `test_concurrent_misses_on_both_servers_fetch_once` | lease загрузки: 24 одновременных промаха в 4 процессах — один запрос к t.me |
| `test_shared_rate_limit_counts_a_client_on_every_server` | `RATE_LIMIT_SHARED`: клиент ограничен, хотя ни один процесс в одиночку не видит достаточно запросов |
| `test_proof_of_work_from_one_server_is_accepted_by_the_other` | challenge одного сервера решает запрос к другому; повтор, подделка |
| `test_rolling_upgrade.py` | предыдущий релиз (`TME_PREVIOUS_IMAGE`) и новый читают записи кэша друг друга |
| `test_valkey_outage.py` | Valkey останавливается — оба сервера продолжают отвечать; возвращается — снова делят кэш |

```bash
make docker
TME_TEST_IMAGE=telegram-me:dev TME_PREVIOUS_IMAGE=docker.io/koval01/telegram-me:latest make integration
```

Они общаются только по HTTP и с Docker, поэтому запускаются без установки
проекта (`uv run --only-group integration`) и пропускаются без
`TME_TEST_IMAGE`.

## Скрипты развёртывания { #deploy-scripts }

`tests/deploy/test_remote_deploy.py` запускает `deploy/remote-deploy.sh` на
локальном Docker так же, как на продакшен-сервере (`deploy/compose.prod.yaml`,
Valkey и поддельный t.me доступны через шлюз сети стека): обновление с
предыдущего релиза, релиз, который так и не становится healthy, релиз,
который healthy, но падает на каждом запросе страницы (это замечает только
smoke-тест), проверку canary (`--verify`), `--rollback` и профиль `proxy` с
Caddy. `tests/deploy/test_rollout.py` запускает `deploy/rollout.sh` — логику
флота из workflow развёртывания — с заглушками `ssh` и `scp`: сначала canary,
а при сбое следующего сервера, canary или публичного smoke-теста — откат
каждого сервера, получившего релиз. Нужен bash 4.4 или новее; тест входит в
`make test`.

```bash
TME_TEST_IMAGE=telegram-me:dev make deploy-test
```

## Пороги покрытия { #coverage-floors }

CI падает, когда покрытие строк опускается ниже порога: `fail_under` в
`pyproject.toml` для Python, `[package.metadata.coverage] fail-under-lines`
в `rust/telegram-me-native/Cargo.toml` для Rust (`cargo llvm-cov`). Workflow
**Guards** роняет pull request, который понижает порог
(`scripts/coverage_floor.py`): пороги только растут — вместе с тестами,
которые их обеспечивают.

## Живой smoke-тест { #live-smoke-test }

`scripts/smoke_matrix.py` проверяет запущенный экземпляр на живом t.me.
Кроме кодов статуса он проверяет содержимое основных ответов (данные канала,
полные посты, пагинацию в нужную сторону, оценки в ленте): изменение
разметки t.me обычно по-прежнему отвечает 200, только с пустыми полями.

```bash
make up && make smoke
uv run --no-project python scripts/smoke_matrix.py --base-url https://api.example.com --preset auto
```

Он использует только стандартную библиотеку и только страницы, которые тоже
записаны, поэтому проходит и на поддельном t.me. CI запускает его на только
что собранном образе в каждом pull request, каждое развёртывание — на каждом
сервере и через публичный URL (см.
[Продакшен](../deployment/production.md#how-a-deployment-works)), а workflow
**Nightly** — на живом t.me и на продакшене.

## Статические проверки { #static-checks }

Помимо тестов, каждый коммит проходит проверки, которые читают код, не
запуская его. Локально их запускают `make lint` и `make audit` (`make check`
включает обе); `make tools` устанавливает бинарники, которые не приносит
`uv sync`.

| Инструмент | Находит | Настройки |
|---|---|---|
| clippy, pedantic и отобранные линты restriction и nursery | в нативном крейте: `unwrap()`, `panic!`, `todo!`, `dbg!`, печать, `#[allow]` без причины; непроверенную индексацию и срезы строк в парсере | `[lints]` в `rust/telegram-me-native/Cargo.toml`, `clippy.toml` |
| cargo-deny | уязвимости из RustSec, заброшенные и отозванные (yanked) крейты, лицензии вне списка разрешённых, две версии одного крейта, неизвестные реестры и git-источники | `rust/telegram-me-native/deny.toml` |
| cargo-machete | зависимости Rust, которые не использует никакой код | |
| mypy `--strict`, плюс `warn_unreachable` и ещё двенадцать кодов ошибок | недостижимый код, переопределения без `@override`, возможно неопределённые имена, всегда истинные условия, неполный `match` | `[tool.mypy]` |
| ruff | bugbear, bandit, ... а теперь и неиспользуемые аргументы, слепой `except`, даты без часового пояса, закомментированный код, приватные члены чужих классов, относительные импорты и `telegram_me_native._native` | `[tool.ruff]` |
| import-linter | импорт вопреки слоям: `api` → `container` → `services` → `cache` → `telegram` → `schemas` → `settings`, и FastAPI или strawberry ниже `api` | `[tool.importlinter]` |
| vulture | функции, атрибуты и переменные в `src/`, которые ничто в `src/` не использует (использования только в тестах мало) | `[tool.vulture]`, `tests/vulture_whitelist.py` |
| `uv audit` | известные уязвимости пакетов из `uv.lock` | |
| gitleaks | секреты в подготовленных файлах (pre-commit) и в каждом коммите (CI), в том числе ключ обхода Cloudflare | `.gitleaks.toml` |
| zizmor | риски GitHub Actions: инъекции в шаблонах, сохранённые учётные данные, унаследованные секреты, Dependabot без выдержки | `.github/zizmor.yml` |
| hadolint | ошибки Dockerfile, вплоть до стиля | `.hadolint.yaml` |
| typos | опечатки в коде и документации (английские и русские страницы; испанские пропускаются) | `.typos.toml` |
| shellcheck, actionlint | скрипты развёртывания и workflows | |
| trivy | высокие и критические уязвимости с исправлением в собранном образе | `make image-scan` |
| проверка зависимостей | уязвимые или под лицензией GPL зависимости, которые добавляет pull request | `.github/workflows/dependency-review.yml` |
| OpenSSF Scorecard | практики цепочки поставок репозитория, в виде оповещений code scanning | `.github/workflows/scorecard.yml` |

Замечание исправляют или разрешают настолько узко, насколько позволяет
инструмент, с причиной рядом: `#[expect(lint, reason = "...")]` в Rust
(`#[allow]` не проходит), `# noqa: CODE - почему` в Python, запись с
комментарием в настройках инструмента. Ничто не отключается для целого
файла ради того, чтобы проверка прошла.

## Задачи CI { #ci-jobs }

| Workflow, задача | Когда | Длится | Ловит |
|---|---|---|---|
| CI: Rust | каждый push | ~3 мин | fmt, clippy, cargo-deny и cargo-machete (+15 с), тесты Rust (враждебный HTML), порог покрытия Rust |
| CI: Lint & types | каждый push | ~4 мин | ruff, mypy, import-linter, vulture, `uv audit`, typos, shellcheck, actionlint, zizmor, hadolint, gitleaks по всей истории (вместе +30 с) |
| CI: Tests | каждый push | ~2 мин | набор выше на Python 3.13 и 3.14, порог покрытия Python |
| CI: Docker image smoke test | после трёх выше | ~2 мин | образ стартует в защищённом режиме, smoke-матрица на живом t.me, корректная остановка |
| CI: Image vulnerabilities | после образа | ~1 мин | trivy: высокие и критические уязвимости с исправлением |
| CI: Integration | после образа | ~2 мин | [интеграционные тесты](#integration-tests) |
| CI: Deploy scripts | после образа | ~2 мин | [тесты скриптов развёртывания](#deploy-scripts) |
| Guards | каждый pull request, изменение меток | ~10 с | [несовместимые изменения API](#breaking-changes), пониженные пороги покрытия |
| Dependency review | каждый pull request | ~15 с | добавленные зависимости с известными уязвимостями или лицензией GPL |
| Scorecard | push в `main`, по понедельникам | ~1 мин | OpenSSF Scorecard (читает только ветку по умолчанию) |
| Nightly | ежедневно в 03:23 UTC | ~15 мин | smoke-тесты на живом t.me и продакшене, 10 мин фаззинга, долгие прогоны свойств; открывает issue с меткой `nightly` |

Release (push в `main`) запускает CI и Guards до сборки и развёртывания
образа.
