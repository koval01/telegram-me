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

Swagger UI para explorar la API de forma interactiva. Se desactiva con
[`DISABLE_DOCS=true`](../configuration.md#disable_docs).

## `GET /openapi.json`

La descripción OpenAPI 3.1 de la API, apta para generadores de clientes. Se
desactiva junto con Swagger UI.

## `GET /static/{path}`

Archivos estáticos incluidos en el paquete (actualmente un `hello.txt` que
algunos despliegues usan para comprobar la accesibilidad).

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
