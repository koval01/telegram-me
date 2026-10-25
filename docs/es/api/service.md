# Endpoints de servicio

## `GET /healthz`

Sonda de disponibilidad (liveness) que usan el `HEALTHCHECK` de Docker, compose
y los balanceadores de carga.

```json
{"status": "ok"}
```

No toca t.me ni Redis, así que sigue en verde aunque el origen no esté
disponible; en ese caso la propia API se degrada de forma controlada (las
caídas de Redis pasan a funcionar «sin caché», las caídas de t.me producen
`503` en las solicitudes afectadas).

## `GET /`

La página de inicio: qué es el servicio, un recuadro **Pruébalo** que llama a
`GET /v1/body/{channel}` desde el navegador, el estado de la instancia (salud,
versión, funciones activas) y enlaces a la documentación, la referencia de la
API, GraphQL y el código fuente. Cuando el servidor exige
[prueba de trabajo](protection.md#proof-of-work), el navegador la resuelve
antes de la solicitud, en Web Workers. Los enlaces antiguos a Swagger UI, que
antes estaba aquí (`/#/Channel/...`), se redirigen a `/api`.

## `GET /api`

Swagger UI para explorar `/openapi.json` de forma interactiva. Con
[`ENABLE_POW`](../configuration.md#enable_pow), **Try it out** resuelve la
prueba de trabajo de cada solicitud.

## `GET /openapi.json`

La descripción OpenAPI 3.1 de la API, apta para generadores de clientes.

## `GET /docs/`

Esta documentación: en inglés en `/docs/`, en español en `/docs/es/` y en
ruso en `/docs/ru/`. La imagen de Docker la incluye, compilada para el prefijo
`/docs/`, así que funciona solo con `docker run` o compose, sin proxy inverso,
y siempre corresponde a la versión que la sirve
([`DOCS_SITE_DIR`](../configuration.md#docs_site_dir)). Los enlaces canónicos
son relativos a la instancia. Las páginas se guardan en caché 10 minutos, y
los recursos con un hash del contenido en el nombre, un año.

Sin compilación (una instalación desde el código fuente), `/docs/…` redirige
a la misma página en <https://koval01.github.io/telegram-me/>.

## `GET /robots.txt`, `GET /sitemap.xml` { #robots-and-sitemap }

`robots.txt` permite a los rastreadores leer las páginas y la documentación,
los mantiene fuera de `/v1/` y `/graphql` e indica el sitemap. `sitemap.xml`
enumera la página de inicio, `/api` y cada página de la documentación en cada
idioma, con sus traducciones como alternativas `hreflang`. Sus URL son
absolutas, sobre [`PUBLIC_URL`](../configuration.md#public_url).

[`DISABLE_DOCS=true`](../configuration.md#disable_docs) elimina `/`, `/api`,
`/openapi.json`, `/docs/` y `/sitemap.xml`; `robots.txt` pasa entonces a
prohibirlo todo.

## `GET /static/{path}`

Estilos, scripts e icono de la página de inicio y de la referencia de la API,
y un `hello.txt` que algunos despliegues usan para comprobar la accesibilidad.

## Métricas { #metrics }

Cuando se define [`METRICS_PORT`](../configuration.md#metrics_port), granian
expone métricas de Prometheus en ese puerto en `/metrics` (actualizadas cada
15 s), por ejemplo:

| Métrica | Significado |
|---|---|
| `granian_requests_handled` | Solicitudes atendidas por worker |
| `granian_connections_active`, `granian_connections_handled`, `granian_connections_err` | Contadores de conexiones |
| `granian_py_wait_cumulative` | Tiempo de espera del bucle de eventos de Python |
| `granian_workers_spawns`, `granian_workers_respawns_for_err`, `granian_workers_respawns_for_rss` | Ciclo de vida de los workers |
| `granian_worker_lifetime` | Segundos desde que arrancó cada worker |

Vincúlalo a una interfaz privada: el listener de métricas usa `HOST`.
