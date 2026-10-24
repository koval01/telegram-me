# Imagen de Docker

## Registros y etiquetas { #registries-and-tags }

La imagen se construye para `linux/amd64` y `linux/arm64` y se publica en:

- `ghcr.io/koval01/telegram-me`
- `docker.io/koval01/telegram-me`

| Etiqueta | Apunta a |
|---|---|
| `latest` | la última build de `main` |
| `sha-<12 hex>` | un commit exacto: úsala para fijar la versión y para revertir |
| `2.0.0`, `2.0`, `2` | versiones publicadas |

Cada imagen incluye un SBOM y una atestación de procedencia de la build, y
etiquetas OCI con la versión y el commit.

## Ejecutar { #run }

```bash
docker run -d --name telegram-me \
  -p 8000:3000 \
  --restart unless-stopped \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -e REDIS_URL=redis://cache.internal:6379/0 \
  ghcr.io/koval01/telegram-me:2.0.0
```

Las opciones del medio son un refuerzo de seguridad opcional; la imagen está
pensada para funcionar con todas ellas.

## Qué hay dentro { #what-is-inside }

- Python 3.14 sobre Debian (slim), las dependencias fijadas en `/app/.venv` y
  nada más: ni compiladores ni curl.
- Se ejecuta como el usuario sin privilegios `app` (uid/gid `10001`).
- `ENTRYPOINT ["telegram-me"]`, `CMD ["serve"]`: el servidor es el PID 1 y
  recibe `SIGTERM` directamente, así que `docker stop` termina en mucho menos
  de un segundo una vez completadas las solicitudes en curso.
- `HEALTHCHECK` llama a `/healthz` cada 30 s con el Python incluido.
- Puerto `3000`.

## Comandos { #commands }

```bash
docker run --rm ghcr.io/koval01/telegram-me version          # print the version
docker run -d ... ghcr.io/koval01/telegram-me worker         # feed refresher only
```

## Recursos { #resources }

Medido en 2.0.0 (arm64), consulta
[Rendimiento](../internals/performance.md#memory):

| `WEB_CONCURRENCY` | En reposo | Tras tráfico ligero |
|---|---|---|
| 1 | ~110 MB (proceso del servidor + un worker) | ~145 MB |
| 2 | ~180 MB | ~225 MB |

Calcula aproximadamente 40 MB para el proceso del servidor y 70–110 MB por
worker, más hasta `MEMORY_CACHE_MAX_MB` por worker para la caché dentro del
proceso (un worker con tráfico intenso sin Redis se mantiene en torno a 170 MB
con el valor por defecto de 64; con Redis solo guarda las entradas de los
últimos `MEMORY_CACHE_MAX_TTL` segundos). La memoria liberada se devuelve al sistema cada
[`MEMORY_TRIM_INTERVAL`](../configuration.md#memory_trim_interval) segundos.
[`WORKERS_MAX_RSS_MB`](../configuration.md#workers_max_rss_mb) añade una red de
seguridad. `WEB_CONCURRENCY` toma por defecto el número de CPU disponibles para
el contenedor (máx. 4); defínelo explícitamente cuando limites las CPU.

## Construcción { #building }

```bash
docker build -t telegram-me:dev .
docker build --build-arg PYTHON_VERSION=3.13 -t telegram-me:py313 .
```

La build usa montajes de caché de BuildKit para uv; las dependencias son una
capa aparte, así que los cambios que solo afectan al código se reconstruyen en
segundos.
