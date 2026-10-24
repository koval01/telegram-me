# Docker image

## Registries and tags

The image is built for `linux/amd64` and `linux/arm64` and published to:

- `ghcr.io/koval01/telegram-me`
- `docker.io/koval01/telegram-me`

| Tag | Points to |
|---|---|
| `latest` | the latest build of `main` |
| `sha-<12 hex>` | an exact commit — use it to pin and to roll back |
| `2.0.0`, `2.0`, `2` | releases |

Every image carries an SBOM and build provenance attestation, and OCI labels
with the version and commit.

## Run

```bash
docker run -d --name telegram-me \
  -p 8000:3000 \
  --restart unless-stopped \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -e REDIS_URL=redis://cache.internal:6379/0 \
  ghcr.io/koval01/telegram-me:2.0.0
```

The flags in the middle are optional hardening; the image is built to work
with all of them.

## What is inside

- Python 3.14 on Debian (slim), the locked dependencies in `/app/.venv`,
  nothing else — no compilers, no curl, no pip.
- Runs as the unprivileged user `app`, set by number (`USER 10001:10001`), so
  a runtime can verify it is not root.
- `ENTRYPOINT ["telegram-me"]`, `CMD ["serve"]`: the server is PID 1 and gets
  `SIGTERM` directly, so `docker stop` finishes in well under a second after
  in-flight requests complete.
- `HEALTHCHECK` calls `/healthz` every 30 s using the bundled Python.
- Port `3000`.

## Commands

```bash
docker run --rm ghcr.io/koval01/telegram-me version          # print the version
docker run -d ... ghcr.io/koval01/telegram-me worker         # feed refresher only
```

## Resources

Measured on 2.0.0 (arm64), see [Performance](../internals/performance.md#memory):

| `WEB_CONCURRENCY` | Idle | After light traffic |
|---|---|---|
| 1 | ~110 MB (server process + one worker) | ~145 MB |
| 2 | ~180 MB | ~225 MB |

Plan for roughly 40 MB for the server process and 70–110 MB per worker, plus
up to `MEMORY_CACHE_MAX_MB` per worker for the in-process cache (a worker
under heavy traffic without Redis stays around 170 MB with the default 64;
with Redis it holds only the last `MEMORY_CACHE_MAX_TTL` seconds of entries). Freed memory is
returned to the system every
[`MEMORY_TRIM_INTERVAL`](../configuration.md#memory_trim_interval) seconds.
[`WORKERS_MAX_RSS_MB`](../configuration.md#workers_max_rss_mb) adds a safety
net. `WEB_CONCURRENCY` defaults to the CPUs available to the container (max
4); set it explicitly when you limit CPUs.

## Building

```bash
docker build -t telegram-me:dev .
docker build --build-arg PYTHON_VERSION=3.13 -t telegram-me:py313 .
```

The build uses BuildKit cache mounts for uv; dependencies are a separate layer,
so code-only changes rebuild in seconds.
