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
  nothing else — no compilers, no curl.
- Runs as the unprivileged user `app` (uid/gid `10001`).
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

Measured on 2.0.0 (arm64, after warming five channels and a feed request):

| `WEB_CONCURRENCY` | Memory |
|---|---|
| 1 | ~140 MB (server process + one worker) |
| 2 | ~210 MB |

Plan for roughly 125 MB plus 70 MB per additional worker, plus up to
`MEMORY_CACHE_MAX_MB` per worker when running without Redis, or set
[`WORKERS_MAX_RSS_MB`](../configuration.md#workers_max_rss_mb) as a safety
net. `WEB_CONCURRENCY` defaults to the CPUs available to the container (max 4);
set it explicitly when you limit CPUs.

## Building

```bash
docker build -t telegram-me:dev .
docker build --build-arg PYTHON_VERSION=3.13 -t telegram-me:py313 .
```

The build uses BuildKit cache mounts for uv; dependencies are a separate layer,
so code-only changes rebuild in seconds.
