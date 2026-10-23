# API overview

The API is versioned in the path (`/v1/...`), speaks JSON over HTTP and needs
no authentication. An OpenAPI 3.1 document is served at `/openapi.json` and
rendered as Swagger UI at `/` (both can be turned off with
[`DISABLE_DOCS`](../configuration.md#disable_docs)).

The same data is also available through a [GraphQL API](graphql.md) at
`/graphql`, where clients select only the fields they need and can combine
several requests into one.

## Conventions

**Channels** are public usernames without `@`: 5–32 characters, starting with
a letter, then letters, digits or `_` (`^[a-zA-Z][a-zA-Z0-9_]{3,31}$`).
Anything else is rejected with `422` before t.me is contacted.

**Post IDs and positions** are integers from `1` to `10 000 000`.

**Text in two forms.** Wherever Telegram shows formatted text, the API returns
an object with `string` (plain text, line breaks as `\n`) and `html` (the
original markup):

```json
{"string": "Pavel Durov", "html": "Pavel Durov"}
```

**Counters are strings** exactly as Telegram formats them (`"10.3M"`,
`"14.2K"`, `"10 281 023 subscribers"`); the API does not reinterpret them.

**Absent values are `null`.** Optional fields are always present in responses
and set to `null` when Telegram does not provide them, so clients can rely on
a stable shape.

**Dates** are ISO-8601 strings with offset plus a Unix timestamp:

```json
{"string": "2026-10-20T10:00:00+00:00", "unix": 1792490400}
```

## Response headers

| Header | When | Meaning |
|---|---|---|
| `X-App-Node` | always | Host name of the server that answered — handy behind a load balancer |
| `X-Process-Time` | always | Time spent in the application, e.g. `3 ms` |
| `Cache-Control` | requests with a `CF-RAY` header (Cloudflare) | `public, max-age=10, stale-while-revalidate=10` (`normal`) or `public, max-age=120, stale-while-revalidate=240` (`aggressive`); none for `off` |
| `X-RateLimit-Limit`, `X-RateLimit-Remaining` | rate limiting enabled | Per-IP budget for the current minute |
| `Retry-After` | `429` responses | Seconds to wait (always `60`) |

CORS is open (`Access-Control-Allow-Origin` mirrors the request origin) unless
restricted with [`CORS_ALLOW_ORIGINS`](../configuration.md#cors_allow_origins).

## Freshness

Responses come from a cache that lives for a few seconds up to an hour,
depending on the endpoint and [`CACHE_MODE`](../configuration.md#cache_mode):

| Endpoint | `off` | `normal` | `aggressive` |
|---|---|---|---|
| `/v1/body`, `/v1/post`, `/v1/more` | always live | 10 s | 120 s |
| `/v1/preview`, `/v1/previews` | [`PREVIEW_CACHE_TTL`](../configuration.md#preview_cache_ttl) (1 h) | same | same |
| `/v1/feed` | see [Feed](feed.md#freshness) | | |

Identical requests that arrive while t.me is being queried share that single
upstream request.

## Status codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `404` | Channel or post does not exist (or is private) |
| `422` | Invalid input — see [Errors](errors.md#422-validation-errors) |
| `429` | Rate limit exceeded |
| `502` | t.me answered with a page the parser could not interpret |
| `503` | t.me is unreachable / answered with an error, or the feature is disabled |

All error bodies are described in [Errors](errors.md).
