# Служебные эндпоинты

## `GET /healthz`

Проверка живости, которую используют `HEALTHCHECK` в Docker, compose и
балансировщики нагрузки.

```json
{"status": "ok"}
```

Она не обращается ни к t.me, ни к Redis, поэтому остаётся зелёной, даже когда
источник недоступен, — сам API в этом случае деградирует плавно (при сбое
Redis работает «без кэша», при сбое t.me затронутые запросы получают `503`).

## `GET /`

Swagger UI для интерактивного изучения API. Отключается через
[`DISABLE_DOCS=true`](../configuration.md#disable_docs).

## `GET /openapi.json`

Описание API в формате OpenAPI 3.1, подходящее для генераторов клиентов.
Отключается вместе со Swagger UI.

## `GET /static/{path}`

Статические файлы, входящие в пакет (сейчас это `hello.txt`, который
некоторые развёртывания используют для проверки доступности).

## Метрики { #metrics }

Если задан [`METRICS_PORT`](../configuration.md#metrics_port), granian
отдаёт метрики Prometheus на этом порту по пути `/metrics` (обновляются
каждые 15 с), например:

| Метрика | Значение |
|---|---|
| `granian_requests_handled` | Обработанные запросы по воркерам |
| `granian_connections_active`, `granian_connections_handled`, `granian_connections_err` | Счётчики соединений |
| `granian_py_wait_cumulative` | Время ожидания цикла событий Python |
| `granian_workers_spawns`, `granian_workers_respawns_for_err`, `granian_workers_respawns_for_rss` | Жизненный цикл воркеров |
| `granian_worker_lifetime` | Секунды с момента запуска каждого воркера |

Привязывайте его к внутреннему интерфейсу: слушатель метрик использует
`HOST`.
