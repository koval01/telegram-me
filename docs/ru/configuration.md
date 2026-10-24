# Конфигурация

Всё настраивается через переменные окружения. Они также читаются из `.env` и
`.env.local` в рабочем каталоге (более поздние файлы имеют приоритет, а
настоящие переменные окружения важнее обоих). Имена нечувствительны к
регистру; логические значения принимают `true/false`, `1/0`, `yes/no`,
`on/off`.

Источник истины —
[`settings.py`](https://github.com/koval01/telegram-me/blob/main/src/telegram_me/settings.py).

!!! tip "Пресеты"
    `compose.yaml` загружает один из
    [`deploy/presets/*.env`](https://github.com/koval01/telegram-me/tree/main/deploy/presets)
    (`standard`, `full`, `minimal`) перед вашим `.env`. См.
    [Быстрый старт](getting-started/quickstart.md#2-docker-compose-with-a-shared-cache-recommended).

## Обзор { #overview }

| Переменная | По умолчанию | Группа |
|---|---|---|
| [`HOST`](#host) | `0.0.0.0` | Сервер |
| [`PORT`](#port) | `3000` | Сервер |
| [`WEB_CONCURRENCY`](#web_concurrency) | число CPU, не больше 4 | Сервер |
| [`FORWARDED_ALLOW_IPS`](#forwarded_allow_ips) | `127.0.0.1` | Сервер |
| [`ACCESS_LOG`](#access_log) | `false` | Сервер |
| [`GRACEFUL_TIMEOUT`](#graceful_timeout) | `10` | Сервер |
| [`WORKERS_MAX_RSS_MB`](#workers_max_rss_mb) | — | Сервер |
| [`METRICS_PORT`](#metrics_port) | — | Сервер |
| [`DISABLE_DOCS`](#disable_docs) | `false` | API |
| [`CORS_ALLOW_ORIGINS`](#cors_allow_origins) | `["*"]` | API |
| [`ENABLE_PREVIEWS`](#enable_previews) | `true` | Функции |
| [`ENABLE_FEED`](#enable_feed) | `false` | Функции |
| [`ENABLE_RATE_LIMIT`](#enable_rate_limit) | `false` | Защита |
| [`RATE_LIMIT_RPM`](#rate_limit_rpm) | `120` | Защита |
| [`RATE_LIMIT_BURST`](#rate_limit_burst) | = `RATE_LIMIT_RPM` | Защита |
| [`RATE_LIMIT_SHARED`](#rate_limit_shared) | `true` | Защита |
| [`ENABLE_POW`](#enable_pow) | `false` | Защита |
| [`POW_SECRET`](#pow_secret) | — | Защита |
| [`POW_DIFFICULTY`](#pow_difficulty) | `18` | Защита |
| [`POW_CHALLENGE_TTL`](#pow_challenge_ttl) | `300` | Защита |
| [`POW_BIND_IP`](#pow_bind_ip) | `true` | Защита |
| [`ENABLE_GRAPHQL`](#enable_graphql) | `true` | GraphQL |
| [`GRAPHQL_MAX_DEPTH`](#graphql_max_depth) | `10` | GraphQL |
| [`GRAPHQL_MAX_COMPLEXITY`](#graphql_max_complexity) | `1500` | GraphQL |
| [`GRAPHQL_MAX_ALIASES`](#graphql_max_aliases) | `15` | GraphQL |
| [`GRAPHQL_MAX_TOKENS`](#graphql_max_tokens) | `2000` | GraphQL |
| [`GRAPHQL_MAX_BATCH_SIZE`](#graphql_max_batch_size) | `10` | GraphQL |
| [`CACHE_MODE`](#cache_mode) | `normal` | Производительность |
| [`PARSER_MODE`](#parser_mode) | `full` | Производительность |
| [`MEMORY_TRIM_INTERVAL`](#memory_trim_interval) | `10` | Производительность |
| [`TELEGRAM_TIMEOUT`](#telegram_timeout) | `10` | Источник |
| [`TELEGRAM_MAX_CONCURRENCY`](#telegram_max_concurrency) | `32` | Источник |
| [`REDIS_URL`](#redis_url) | — | Кэш |
| [`REDIS_TIMEOUT`](#redis_timeout) | `0.5` | Кэш |
| [`PREVIEW_CACHE_TTL`](#preview_cache_ttl) | `3600` | Кэш |
| [`MEMORY_CACHE_MAX_MB`](#memory_cache_max_mb) | `64` | Кэш |
| [`FEED_FRESH_TTL`](#feed_fresh_ttl) | `60` | Лента |
| [`FEED_STALE_TTL`](#feed_stale_ttl) | `900` | Лента |
| [`FEED_REFRESH_MODE`](#feed_refresh_mode) | `embedded` | Лента |
| [`FEED_REFRESH_MIN_INTERVAL`](#feed_refresh_min_interval) | `30` | Лента |
| [`FEED_REFRESH_MAX_INTERVAL`](#feed_refresh_max_interval) | `600` | Лента |
| [`FEED_REFRESH_TOP`](#feed_refresh_top) | `200` | Лента |
| [`FEED_POPULARITY_HALF_LIFE`](#feed_popularity_half_life) | `3600` | Лента |
| [`FEED_HALF_LIFE_HOURS`](#feed_half_life_hours) | `24` | Лента |
| [`FEED_DIVERSITY`](#feed_diversity) | `0.5` | Лента |
| [`FEED_COLLAPSE_DUPLICATES`](#feed_collapse_duplicates) | `true` | Лента |
| [`LOG_LEVEL`](#log_level) | `INFO` | Логи |
| [`LOG_FORMAT`](#log_format) | `text` | Логи |

## Сервер { #server }

### `HOST`

Интерфейс, к которому привязывается HTTP-сервер (и слушатель метрик). Внутри
контейнеров оставляйте `0.0.0.0`; для экземпляра без контейнера за локальным
обратным прокси используйте `127.0.0.1`.

### `PORT`

Порт HTTP-сервера. Docker-образ слушает `3000`; публикуйте его через
`-p 8000:3000`.

### `WEB_CONCURRENCY`

Число процессов-воркеров. По умолчанию — число CPU, доступных процессу, но не
больше 4. У каждого воркера свой цикл событий и, без Redis, свой кэш в памяти,
поэтому при нескольких воркерах используйте Redis.

### `FORWARDED_ALLOW_IPS`

Адреса прокси через запятую, заголовкам `X-Forwarded-For` /
`X-Forwarded-Proto` которых можно доверять, или `*`, чтобы доверять любым
(только если порт недоступен напрямую). От этого зависит IP клиента, который
использует ограничитель частоты запросов. Пример для nginx на другом хосте:
`FORWARDED_ALLOW_IPS=10.0.0.5`.

### `ACCESS_LOG`

Логировать каждый запрос (`true`) — по умолчанию выключено, чтобы логи не
шумели при высокой нагрузке.

### `GRACEFUL_TIMEOUT`

Сколько секунд воркер может завершать текущие запросы после `SIGTERM`, прежде
чем его принудительно остановят.

### `WORKERS_MAX_RSS_MB`

Перезапускать воркер, резидентная память которого превысила столько МиБ.
Страховка для хостов с ограниченной памятью; по умолчанию не задано. Воркер
занимает ~60 МиБ в простое и ~100 МиБ под нагрузкой с Redis, плюс до
`MEMORY_CACHE_MAX_MB` без него, так что значение около
`MEMORY_CACHE_MAX_MB + 150` срабатывает только при реальных проблемах.

### `METRICS_PORT`

Отдавать метрики Prometheus сервера на этом порту по пути `/metrics`. См.
[Служебные эндпоинты](api/service.md#metrics).

## API { #api }

### `DISABLE_DOCS`

Скрыть Swagger UI (`/`) и `/openapi.json`, а на `/graphql` — IDE GraphiQL,
`GET`-запросы и интроспекцию схемы.

### `CORS_ALLOW_ORIGINS`

JSON-список разрешённых origin для браузеров, например
`CORS_ALLOW_ORIGINS='["https://example.com"]'`. Значение по умолчанию `["*"]`
разрешает любой origin.

## Функции { #features }

### `ENABLE_PREVIEWS`

Включить `/v1/preview/{channel}` и `/v1/previews`. Когда они отключены, они
отвечают `503`.

### `ENABLE_FEED`

Включить `POST /v1/feed` (и его фоновый рефрешер). **По умолчанию выключено**,
потому что это самый тяжёлый эндпоинт. Когда он отключён, он отвечает `503`.

## Защита { #protection }

Обе выключены по умолчанию; как они работают и что отправляют клиенты, описано в разделе [Ограничение частоты и proof of work](api/protection.md).

### `ENABLE_RATE_LIMIT`

Ограничение частоты запросов по IP клиента для всех эндпоинтов (GCRA):
всплеск до [`RATE_LIMIT_BURST`](#rate_limit_burst) запросов, затем
[`RATE_LIMIT_RPM`](#rate_limit_rpm) в минуту. При превышении API отвечает `429`
с `Retry-After`. За прокси укажите
[`FORWARDED_ALLOW_IPS`](#forwarded_allow_ips), чтобы учитывался настоящий IP клиента.

### `RATE_LIMIT_RPM`

Устойчивый темп: запросов в минуту на IP клиента.

### `RATE_LIMIT_BURST`

Сколько запросов клиент может сделать сразу, прежде чем начнёт действовать
поминутный темп. По умолчанию равно `RATE_LIMIT_RPM`.

### `RATE_LIMIT_SHARED`

При заданном [`REDIS_URL`](#redis_url) каждый клиент считается по всем
воркерам и серверам: раз в секунду каждый процесс добавляет свои счётчики в
Redis и блокирует клиентов, превысивших лимит там. Запросы никогда не ждут
Redis. При `false` или без Redis каждый процесс ограничивает сам по себе.

### `ENABLE_POW`

Требовать [proof of work](api/protection.md#proof-of-work) в каждом запросе
(кроме документации, `/healthz` и эндпоинта задач). Нужен
[`POW_SECRET`](#pow_secret); задачи выдаёт `GET /v1/pow/challenge`.

### `POW_SECRET`

Подписывает задачи (HMAC-SHA256). Не короче 32 символов, **одинаковый на
всех серверах** и секретный: зная его, можно подделывать задачи. Сгенерировать:
`openssl rand -hex 32`. Смена секрета делает выданные задачи недействительными.

### `POW_DIFFICULTY`

Сколько ведущих нулевых бит должно быть в SHA-256 каждого запроса, от 8 до 32.
Каждый бит удваивает работу клиента: 18 — это около 0,2 с на Python и 17 мс в
нативном коде на запрос. См. [Выбор сложности](api/protection.md#choosing-the-difficulty).

### `POW_CHALLENGE_TTL`

Сколько секунд задача остаётся действительной, от 30 до 3600. Всё это время
клиент использует одну задачу для всех своих запросов.

### `POW_BIND_IP`

Принимать задачу только с того IP, которому она выдана, чтобы решённые
задачи нельзя было раздавать. Выключите для клиентов, у которых адрес часто
меняется.

## GraphQL { #graphql }

Как применяются лимиты, описано в разделе [GraphQL](api/graphql.md#limits).

### `ENABLE_GRAPHQL`

Обслуживать [GraphQL API](api/graphql.md) на `/graphql`. Когда он отключён,
путь отвечает `404`. GraphiQL, `GET`-запросы и интроспекция дополнительно
зависят от [`DISABLE_DOCS`](#disable_docs).

### `GRAPHQL_MAX_DEPTH`

Максимально допустимая вложенность выборок ниже корневого поля (у корневого
поля глубина 0; поля интроспекции не считаются).

### `GRAPHQL_MAX_COMPLEXITY`

Максимально допустимая стоимость операции: 1 за каждое выбранное поле плюс 10
за каждый канал, который корневое поле может загрузить с t.me.

### `GRAPHQL_MAX_ALIASES`

Максимальное число алиасов в одном документе.

### `GRAPHQL_MAX_TOKENS`

Максимальная длина документа в токенах лексера GraphQL; ограничивает работу
разбора.

### `GRAPHQL_MAX_BATCH_SIZE`

Максимальное число операций в одном пакетном запросе (JSON-массив). Пакеты
большего размера отклоняются с `400`.

## Производительность { #performance }

### `CACHE_MODE`

| Значение | Кэш страниц | `Cache-Control` для Cloudflare |
|---|---|---|
| `off` | нет — каждый запрос идёт на t.me | нет |
| `normal` | 10 с | `public, max-age=10, stale-while-revalidate=10` |
| `aggressive` | 120 с | `public, max-age=120, stale-while-revalidate=240` |

Действует на `/v1/body`, `/v1/post` и `/v1/more`. У превью и ленты свои
настройки, см. ниже.

### `PARSER_MODE`

| Значение | Что извлекается |
|---|---|
| `full` | всё: текст с сущностями, медиа, опросы, кнопки, ответы, превью ссылок, реакции |
| `simplified` | текст (без сущностей), медиа и кнопки — заметно дешевле по CPU |

### `MEMORY_TRIM_INTERVAL`

Интервал в секундах, с которым освобождённая память кучи возвращается
операционной системе (`malloc_trim`, только glibc — то есть Docker-образ).
Иначе после всплеска трафика glibc удерживает память больших короткоживущих
объектов для повторного использования: воркер под нагрузкой держал ~80 МиБ
такой свободной кучи. Одна очистка занимает около 1 мс. `0` отключает её.

## Источник { #upstream }

### `TELEGRAM_TIMEOUT`

Общий тайм-аут одного запроса к t.me в секундах (фаза подключения ограничена
5 с). Неудачный запрос повторяется один раз на новом соединении, если ошибка
сетевая.

### `TELEGRAM_MAX_CONCURRENCY`

Максимум одновременных запросов к t.me на процесс-воркер. Защищает сервер от
ограничения частоты запросов со стороны Telegram при больших запросах ленты
или превью.

## Кэш { #cache }

### `REDIS_URL`

URL Redis или Valkey, например `redis://valkey:6379/0` или
`rediss://user:pass@host:6380/0`. Если задан, кэш страниц, кэш превью,
популярность для ленты и блокировка лидера рефрешера общие для всех воркеров и
серверов. Если не задан, используется кэш внутри процесса.

Сбой Redis никогда не замедляет запросы: команды прерываются по тайм-ауту
через [`REDIS_TIMEOUT`](#redis_timeout), после чего Redis обходится стороной
30 секунд (circuit breaker), а API тем временем работает без кэша.

Каждый воркер держит до `2 × TELEGRAM_MAX_CONCURRENCY` (не меньше 32)
соединений с Redis. Всплеск команд сверх этого, например несколько запросов
ленты по 100 каналов одновременно, ждёт свободного соединения до
`REDIS_TIMEOUT`; команда, которая так и не получила соединение, считается
промахом кэша и не размыкает circuit breaker.

### `REDIS_TIMEOUT`

Тайм-аут подключения и выполнения команды в секундах, а также максимальное
время ожидания свободного соединения из пула.

### `PREVIEW_CACHE_TTL`

Сколько секунд превью канала хранится в кэше. Неизвестные каналы в любом
случае кэшируются на 60 секунд.

### `MEMORY_CACHE_MAX_MB`

Лимит памяти кэша внутри процесса (на воркер), когда Redis не используется:
записи хранятся сжатыми, а лимит учитывает то, что они реально занимают
(значение, ключ и служебные данные). Первыми вытесняются давно не
использовавшиеся записи. При значении по умолчанию 64 воркер под высокой
нагрузкой держится около 170 МиБ (весь контейнер — около 230 МиБ).

## Лента { #feed }

Всё это имеет значение только при [`ENABLE_FEED=true`](#enable_feed). См.
[Внутреннее устройство → Лента](internals/feed.md).

### `FEED_FRESH_TTL`

Закэшированная страница канала моложе этого значения (в секундах)
используется как есть.

### `FEED_STALE_TTL`

Страница старше `FEED_FRESH_TTL`, но моложе этого значения отдаётся сразу, а
фоновое обновление её заменяет. Более старые страницы загружаются до ответа.

### `FEED_REFRESH_MODE`

| Значение | Поведение |
|---|---|
| `embedded` | рефрешер запускает каждый процесс API; с Redis работу выполняет только один из них (лидер) |
| `worker` | API его не запускает; запустите `telegram-me worker` отдельно (нужен Redis) |
| `off` | без фонового обновления; страницы обновляются только запросами ленты |

### `FEED_REFRESH_MIN_INTERVAL`

Самый короткий интервал обновления (в секундах), для самых популярных
каналов.

### `FEED_REFRESH_MAX_INTERVAL`

Самый длинный интервал обновления, для каналов почти без спроса. Канал с
оценкой популярности *s* обновляется каждые
`clamp(MAX / (1 + s), MIN, MAX)` секунд.

### `FEED_REFRESH_TOP`

Сколько самых популярных каналов обслуживает рефрешер.

### `FEED_POPULARITY_HALF_LIFE`

Период полураспада популярности канала в секундах: по прошествии этого времени
запрос ленты весит вдвое меньше. Каналы с оценкой ниже 0.05 забываются.

### `FEED_HALF_LIFE_HOURS`

Через сколько часов свежесть поста в ранжировании уменьшается вдвое. Меньшие
значения сильнее продвигают самые новые посты.

### `FEED_DIVERSITY`

От `0` (чистый порядок по оценке) до `1` (максимальное перемешивание каналов
и тем). См.
[Внутреннее устройство → Лента](internals/feed.md#diversity).

### `FEED_COLLAPSE_DUPLICATES`

Сворачивать одну и ту же новость, опубликованную несколькими каналами, в её
лучший пост, перечисляя остальные в `_duplicates`.

## Логи { #logging }

### `LOG_LEVEL`

`DEBUG`, `INFO`, `WARNING` или `ERROR`.

### `LOG_FORMAT`

`text` — для людей, `json` — для сборщиков логов (по одному JSON-объекту на
строку с `time`, `level`, `logger`, `message` и любыми структурированными
дополнительными полями).
