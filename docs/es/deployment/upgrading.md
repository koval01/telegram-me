# Actualizar desde 1.x

La API `/v1` es retrocompatible: las rutas, los parámetros y la forma de las
respuestas son los mismos, y los clientes existentes siguen funcionando. Lo que
cambió afecta sobre todo a la operación.

## Cambios de comportamiento { #behaviour-changes }

| Área | 1.x | 2.0 |
|---|---|---|
| `POST /v1/feed` | activado por defecto | **desactivado por defecto**: define `ENABLE_FEED=true` |
| `POST /v1/previews` | `404` para toda la solicitud si falta un canal | devuelve los canales que existen y omite el resto |
| Página no analizable | `400` | `502` |
| Una publicación malformada | fallaba toda la página | la publicación se omite y se registra en el log |
| `?position=` por encima de 999 999 | `422` | se acepta (hasta 10 000 000) |
| Teclados inline | solo la primera fila | todas las filas, aplanadas en orden |
| `forwarded.name.html` | malformado (`<span dir="auto">…`) | el marcado del nombre |
| `forwarded.author` | — | nuevo campo opcional |
| Cabecera `Server` | `uvicorn` | `granian` |

## Cambios de configuración { #configuration-changes }

Todas las variables de 1.x conservan su significado: `PORT`, `DISABLE_DOCS`,
`REDIS_URL`, `ENABLE_FEED`, `ENABLE_PREVIEWS`, `ENABLE_RATE_LIMIT`,
`RATE_LIMIT_RPM`, `CACHE_MODE`, `PARSER_MODE`.

- `REDIS_URL` ya no tiene valor por defecto: sin ella se usa una caché dentro
  del proceso en lugar de intentar `localhost:6379`.
- `VERSION` desaparece; la versión viene del paquete.
- Las opciones nuevas se enumeran en [Configuración](../configuration.md).

## Cambios en el despliegue { #deployment-changes }

- La imagen se arranca con `telegram-me serve` (granian) en lugar de
  `start.sh` (uvicorn). El puerto dentro del contenedor sigue siendo `3000`.
- Se eliminaron los archivos de configuración de Fly.io, Google App Engine y
  Heroku.
- `docker-compose.yml` y `docker-compose.ghcr.yml` se sustituyeron por un único
  [`compose.yaml`](compose.md) con presets.
- Los servidores desplegados con el workflow antiguo se migran automáticamente
  en el primer despliegue de 2.0 (consulta
  [Producción](production.md#how-a-deployment-works)).

## Claves de Redis { #redis-keys }

2.0 guarda su caché bajo el prefijo `tme:v2:`. Las claves escritas por 1.x
(`telegram:preview:*`) se ignoran y caducan por sí solas en menos de dos
horas.
