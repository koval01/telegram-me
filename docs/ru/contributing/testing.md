# Тестирование

```bash
make test        # ~160 tests, about 2 seconds, no network
```

Тесты никогда не обращаются к t.me. Честность сервиса обеспечивают тесты двух
видов.

## Снапшоты контракта { #contract-snapshots }

`tests/test_snapshots.py` вызывает каждый эндпоинт на **записанных страницах
t.me** и сравнивает полный JSON-ответ с файлом в `tests/snapshots/`. Любое
изменение того, что возвращает API, — переименованное поле, другое число,
пропавший пост — роняет тест и видно как читаемый diff.

Записанные страницы хранятся в `tests/fixtures/telegram/` (gzip) вместе с
`manifest.json`, где описано, на какой запрос отвечает каждый файл.
`FixtureFetcher` подменяет сетевой слой и роняет тест при любом запросе,
который не был записан. Время замораживается с помощью `time-machine`, потому
что оценка в ленте зависит от «сейчас».

Покрыты шесть каналов (`durov`, `telegram`, `stranaua`, `tginfo`,
`nebudetafk`, `nebudetgg`), оба режима парсера, пагинация в обе стороны,
превью, несуществующий канал, лента и отдельные посты с inline-кнопкой,
опросом, стикером, ответом, превью ссылки и голосовым сообщением.

`tests/test_graphql_contract.py` делает то же самое для GraphQL: полная схема
(SDL) в `tests/snapshots/graphql/schema.graphql`, снапшот на каждый запрос и
проверки того, что каждый результат GraphQL совпадает с соответствующим
REST-ответом.

### Намеренное изменение вывода { #changing-the-output-on-purpose }

```bash
make snapshots                 # UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py tests/test_graphql_contract.py
git diff --stat tests/snapshots
git diff tests/snapshots        # review every change
```

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
| `test_cache.py` | LRU в памяти с лимитом памяти, сжатием и истечением срока, бэкенд Redis, circuit breaker |
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

Тесты поддерживают async (`pytest-asyncio` в режиме auto), а предупреждения
считаются ошибками.

## Память { #memory }

`tests/test_memory.py` (маркер `memory`) отправляет тысячи запросов напрямую в
ASGI-приложение по алиасам записанных каналов (`tests/workload.py`) и падает,
если аллокации Python или, в Linux, резидентная память продолжают расти после
прогрева, или если кэш в памяти превышает свой лимит. Он входит в запуск по
умолчанию, поэтому CI его выполняет; локально его можно пропустить через
`-m "not memory"`.

`scripts/memory_profile.py` выводит стоящие за этим цифры: стоимость импорта
главного процесса и воркера, размер одной закэшированной страницы, а также RSS
и аллокации Python, замеренные во время длинной нагрузки:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 50
```

RSS читается из `/proc`, поэтому для осмысленных цифр запускайте его в Linux.

## Живой smoke-тест { #live-smoke-test }

`scripts/smoke_matrix.py` проверяет запущенный экземпляр на живом t.me:

```bash
make up && make smoke
uv run --no-project python scripts/smoke_matrix.py --base-url https://api.example.com --preset auto
```

CI запускает его на только что собранном образе в каждом pull request.
