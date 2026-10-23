# Inicio rápido

Tres formas de poner en marcha una instancia, de la más rápida a la más
completa. Todas exponen la API en **<http://localhost:8000>**, con Swagger UI
interactivo en la raíz.

## 1. Un solo contenedor { #1-a-single-container }

```bash
docker run -d --name telegram-me -p 8000:3000 --restart unless-stopped \
  ghcr.io/koval01/telegram-me:latest
```

La imagen también se publica en Docker Hub como `koval01/telegram-me`. Sin
más configuración usa una caché dentro del proceso, ideal para una única
instancia pequeña.

Compruébalo:

```bash
curl http://localhost:8000/healthz          # {"status":"ok"}
curl http://localhost:8000/v1/body/durov    # channel page as JSON
```

## 2. Docker Compose con una caché compartida (recomendado) { #2-docker-compose-with-a-shared-cache-recommended }

El repositorio incluye un `compose.yaml` que ejecuta la API junto a una caché
[Valkey](https://valkey.io/) (compatible con Redis):

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
docker compose up -d --wait
```

Elige un preset con `PRESET`:

| Preset | Feed | Vistas previas | Límite de solicitudes | Caché | Parser |
|---|---|---|---|---|---|
| `standard` *(por defecto)* | no | sí | no | `normal` | `full` |
| `full` | sí | sí | no | `normal` | `full` |
| `minimal` | no | no | 90 sol./min por IP | `aggressive` | `simplified` |

```bash
PRESET=full docker compose up -d --wait
```

Todo lo demás se define en un archivo `.env` junto a `compose.yaml`: copia
[`.env.example`](https://github.com/koval01/telegram-me/blob/main/.env.example)
y consulta [Configuración](../configuration.md) para ver todas las opciones.

## 3. Desde el código fuente { #3-from-source }

Requiere [uv](https://docs.astral.sh/uv/) (instala por sí mismo una versión de
Python adecuada):

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
uv sync
uv run telegram-me serve --reload   # http://localhost:3000
```

Consulta [Desarrollo local](local-development.md) para el flujo de trabajo
completo.

## Próximos pasos { #next-steps }

- Explora la [referencia de la API](../api/index.md).
- Colócalo detrás de un [proxy inverso](../deployment/reverse-proxy.md).
- Ejecuta varios servidores con
  [despliegues escalonados](../deployment/production.md).
