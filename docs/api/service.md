# Service endpoints

## `GET /healthz`

Liveness probe used by the Docker `HEALTHCHECK`, compose and load balancers.

```json
{"status": "ok"}
```

It does not touch t.me or Redis, so it stays green while upstream is
unavailable — the API itself degrades gracefully in that case (Redis outages
fall back to "no cache", t.me outages produce `503` for affected requests).

## `GET /`

Swagger UI for interactive exploration. Disabled by
[`DISABLE_DOCS=true`](../configuration.md#disable_docs).

## `GET /openapi.json`

The OpenAPI 3.1 description of the API, suitable for client generators.
Disabled together with the Swagger UI.

## `GET /static/{path}`

Static files bundled with the package (currently a `hello.txt` used as a
reachability check by some deployments).

## Metrics

When [`METRICS_PORT`](../configuration.md#metrics_port) is set, granian exposes
Prometheus metrics on that port at `/metrics` (refreshed every 15 s), for
example:

| Metric | Meaning |
|---|---|
| `granian_requests_handled` | Requests served per worker |
| `granian_connections_active`, `granian_connections_handled`, `granian_connections_err` | Connection counters |
| `granian_py_wait_cumulative` | Time spent waiting for the Python event loop |
| `granian_workers_spawns`, `granian_workers_respawns_for_err`, `granian_workers_respawns_for_rss` | Worker lifecycle |
| `granian_worker_lifetime` | Seconds since each worker started |

Bind it to a private interface: the metrics listener uses `HOST`.
