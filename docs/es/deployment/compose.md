# Docker Compose

[`compose.yaml`](https://github.com/koval01/telegram-me/blob/main/compose.yaml),
en la raíz del repositorio, ejecuta una instancia completa en un solo host:

| Servicio | Imagen | Propósito |
|---|---|---|
| `api` | `ghcr.io/koval01/telegram-me` (o una build local) | La API en `${APP_PORT:-8000}` |
| `valkey` | `valkey/valkey:9.1-alpine` | Caché compartida (compatible con Redis), solo en memoria, expulsión LRU |
| `worker` | la misma que `api`, `command: worker` | Actualizador del feed opcional (perfil `worker`) |

## Uso { #usage }

```bash
docker compose up -d --wait                       # published image
docker compose up -d --build --wait               # build from the checkout
PRESET=full docker compose up -d --wait           # enable the feed
docker compose --profile worker up -d --wait      # + standalone feed worker
docker compose logs -f api
docker compose down
```

`--wait` termina cuando todos los servicios están sanos.

## Configuración { #configuration }

Los ajustes se aplican por capas; la posterior tiene prioridad:

1. `deploy/presets/${PRESET:-standard}.env`
2. `.env` junto a `compose.yaml` (opcional)
3. `REDIS_URL` apunta por defecto al Valkey incluido (`redis://valkey:6379/0`)
   salvo que lo definas en el entorno de la shell.

Variables útiles para el propio compose:

| Variable | Por defecto | Significado |
|---|---|---|
| `PRESET` | `standard` | `standard`, `full` o `minimal` |
| `APP_PORT` | `8000` | Puerto del host |
| `APP_IMAGE` | `ghcr.io/koval01/telegram-me:latest` | Imagen que se ejecuta |
| `VALKEY_MAXMEMORY` | `256mb` | Tamaño de la caché |

## Refuerzo de seguridad { #hardening }

Los dos servicios de la aplicación se ejecutan con un sistema de archivos raíz
de solo lectura, un `tmpfs` en `/tmp`, todas las capabilities de Linux
eliminadas, `no-new-privileges`, rotación de logs (5 × 20 MB) y
`restart: unless-stopped`. Valkey no guarda datos en disco: la caché se
reconstruye desde t.me tras un reinicio.
