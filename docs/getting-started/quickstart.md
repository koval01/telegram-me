# Quickstart

Three ways to get an instance running, from quickest to most complete. All of
them expose the API on **<http://localhost:8000>**, with the interactive
Swagger UI at the root.

## 1. A single container

```bash
docker run -d --name telegram-me -p 8000:3000 --restart unless-stopped \
  ghcr.io/koval01/telegram-me:latest
```

The image is also published on Docker Hub as `koval01/telegram-me`. Without
further configuration it uses an in-process cache, which is ideal for a
single small instance.

Check it:

```bash
curl http://localhost:8000/healthz          # {"status":"ok"}
curl http://localhost:8000/v1/body/durov    # channel page as JSON
```

## 2. Docker Compose with a shared cache (recommended)

The repository ships a `compose.yaml` that runs the API next to a
[Valkey](https://valkey.io/) (Redis-compatible) cache:

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
docker compose up -d --wait
```

Pick a preset with `PRESET`:

| Preset | Feed | Previews | Rate limit | Cache | Parser |
|---|---|---|---|---|---|
| `standard` *(default)* | off | on | off | `normal` | `full` |
| `full` | on | on | off | `normal` | `full` |
| `minimal` | off | off | 90 req/min per IP | `aggressive` | `simplified` |

```bash
PRESET=full docker compose up -d --wait
```

Anything else is set in a `.env` file next to `compose.yaml` — copy
[`.env.example`](https://github.com/koval01/telegram-me/blob/main/.env.example)
and see [Configuration](../configuration.md) for every option.

## 3. From source

Requires [uv](https://docs.astral.sh/uv/) (it installs a suitable Python by
itself):

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
uv sync
uv run telegram-me serve --reload   # http://localhost:3000
```

See [Local development](local-development.md) for the full workflow.

## Next steps

- Explore the [API reference](../api/index.md).
- Put it behind a [reverse proxy](../deployment/reverse-proxy.md).
- Run several servers with [rolling deployments](../deployment/production.md).
