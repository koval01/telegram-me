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

The landing page: what the service is, a **Try it** box that calls
`GET /v1/body/{channel}` from the browser, the state of the instance (health,
version, enabled features) and links to the documentation, the API
reference, GraphQL and the source code. When the server requires
[proof of work](protection.md#proof-of-work), the browser solves it before
the request, on Web Workers. Old links to the Swagger UI that used to live
here (`/#/Channel/...`) are forwarded to `/api`.

## `GET /api`

Swagger UI for interactive exploration of `/openapi.json`. With
[`ENABLE_POW`](../configuration.md#enable_pow), **Try it out** solves the
proof of work for every request.

## `GET /openapi.json`

The OpenAPI 3.1 description of the API, suitable for client generators.

## `GET /docs/`

This documentation: English at `/docs/`, Spanish at `/docs/es/`, Russian at
`/docs/ru/`. The Docker image ships it, built for the `/docs/` prefix, so it
works with `docker run` and compose alone, without a reverse proxy, and always
matches the version that serves it
([`DOCS_SITE_DIR`](../configuration.md#docs_site_dir)). Canonical links are
relative to the instance. Pages are cached for 10 minutes, assets with a
content hash in their name for a year.

Without a build (an install from source), `/docs/…` redirects to the same
page on <https://koval01.github.io/telegram-me/>.

## `GET /robots.txt`, `GET /sitemap.xml` { #robots-and-sitemap }

`robots.txt` lets crawlers read the pages and the documentation, keeps them
off `/v1/` and `/graphql`, and points at the sitemap. `sitemap.xml` lists the
landing page, `/api` and every documentation page in every language, with
its translations as `hreflang` alternates. Its URLs are absolute, on
[`PUBLIC_URL`](../configuration.md#public_url).

[`DISABLE_DOCS=true`](../configuration.md#disable_docs) removes `/`, `/api`,
`/openapi.json`, `/docs/` and `/sitemap.xml`; `robots.txt` then disallows
everything.

## `GET /static/{path}`

Styles, scripts and the icon of the landing page and the API reference, and a
`hello.txt` that some deployments use as a reachability check.

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
