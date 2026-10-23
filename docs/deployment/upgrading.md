# Upgrading from 1.x

The `/v1` API is backward compatible: paths, parameters and response shapes
are the same, and existing clients keep working. What changed is mostly
around operations.

## Behaviour changes

| Area | 1.x | 2.0 |
|---|---|---|
| `POST /v1/feed` | enabled by default | **disabled by default** — set `ENABLE_FEED=true` |
| `POST /v1/previews` | `404` for the whole request if one channel is missing | returns the channels that exist, omits the rest |
| Unparsable page | `400` | `502` |
| One malformed post | the whole page failed | the post is skipped and logged |
| `?position=` above 999 999 | `422` | accepted (up to 10 000 000) |
| Inline keyboards | first row only | all rows, flattened in order |
| `forwarded.name.html` | malformed (`<span dir="auto">…`) | the name markup |
| `forwarded.author` | — | new optional field |
| `Server` header | `uvicorn` | `granian` |

## Configuration changes

All 1.x variables keep their meaning: `PORT`, `DISABLE_DOCS`, `REDIS_URL`,
`ENABLE_FEED`, `ENABLE_PREVIEWS`, `ENABLE_RATE_LIMIT`, `RATE_LIMIT_RPM`,
`CACHE_MODE`, `PARSER_MODE`.

- `REDIS_URL` has no default any more: without it an in-process cache is used
  instead of trying `localhost:6379`.
- `VERSION` is gone; the version comes from the package.
- New options are listed in [Configuration](../configuration.md).

## Deployment changes

- The image is started by `telegram-me serve` (granian) instead of
  `start.sh` (uvicorn). The port inside the container is still `3000`.
- Fly.io, Google App Engine and Heroku configuration files were removed.
- `docker-compose.yml` and `docker-compose.ghcr.yml` were replaced by a single
  [`compose.yaml`](compose.md) with presets.
- Servers deployed by the old workflow are migrated automatically on the first
  2.0 deployment (see [Production](production.md#how-a-deployment-works)).

## Redis keys

2.0 stores its cache under the `tme:v2:` prefix. Keys written by 1.x
(`telegram:preview:*`) are ignored and expire on their own within two hours.
