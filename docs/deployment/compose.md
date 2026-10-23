# Docker Compose

[`compose.yaml`](https://github.com/koval01/telegram-me/blob/main/compose.yaml)
in the repository root runs a complete instance on one host:

| Service | Image | Purpose |
|---|---|---|
| `api` | `ghcr.io/koval01/telegram-me` (or a local build) | The API on `${APP_PORT:-8000}` |
| `valkey` | `valkey/valkey:9.1-alpine` | Shared cache (Redis-compatible), memory only, LRU eviction |
| `worker` | same as `api`, `command: worker` | Optional feed refresher (profile `worker`) |

## Usage

```bash
docker compose up -d --wait                       # published image
docker compose up -d --build --wait               # build from the checkout
PRESET=full docker compose up -d --wait           # enable the feed
docker compose --profile worker up -d --wait      # + standalone feed worker
docker compose logs -f api
docker compose down
```

`--wait` returns once every service is healthy.

## Configuration

Settings are layered, later wins:

1. `deploy/presets/${PRESET:-standard}.env`
2. `.env` next to `compose.yaml` (optional)
3. `REDIS_URL` defaults to the bundled Valkey (`redis://valkey:6379/0`) unless
   you set it in the shell environment.

Useful variables for compose itself:

| Variable | Default | Meaning |
|---|---|---|
| `PRESET` | `standard` | `standard`, `full` or `minimal` |
| `APP_PORT` | `8000` | Host port |
| `APP_IMAGE` | `ghcr.io/koval01/telegram-me:latest` | Image to run |
| `VALKEY_MAXMEMORY` | `256mb` | Cache size |

## Hardening

Both application services run with a read-only root filesystem, a `tmpfs` at
`/tmp`, all Linux capabilities dropped, `no-new-privileges`, log rotation
(5 × 20 MB) and `restart: unless-stopped`. Valkey keeps no data on disk: the
cache is rebuilt from t.me after a restart.
