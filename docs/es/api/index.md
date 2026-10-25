# Resumen de la API

La API se versiona en la ruta (`/v1/...`), habla JSON sobre HTTP y no requiere
autenticación. En `/openapi.json` se sirve un documento OpenAPI 3.1, que se
muestra como Swagger UI en [`/api`](service.md#get-api) (ambos se pueden
desactivar con [`DISABLE_DOCS`](../configuration.md#disable_docs)).

Los mismos datos están disponibles también mediante una
[API GraphQL](graphql.md) en `/graphql`, donde los clientes seleccionan solo
los campos que necesitan y pueden combinar varias solicitudes en una.

## Convenciones { #conventions }

**Los canales** son nombres de usuario públicos sin `@`: de 5 a 32 caracteres,
empezando por una letra, seguida de letras, dígitos o `_`
(`^[a-zA-Z][a-zA-Z0-9_]{3,31}$`). Cualquier otra cosa se rechaza con `422`
antes de contactar con t.me.

**Los ID y posiciones de publicaciones** son enteros de `1` a `10 000 000`.

**Texto en dos formas.** Allí donde Telegram muestra texto con formato, la API
devuelve un objeto con `string` (texto plano, saltos de línea como `\n`) y
`html` (el marcado original):

```json
{"string": "Pavel Durov", "html": "Pavel Durov"}
```

**Los contadores son cadenas** exactamente con el formato de Telegram
(`"10.3M"`, `"14.2K"`, `"10 281 023 subscribers"`); la API no los
reinterpreta.

**Los valores ausentes son `null`.** Los campos opcionales siempre están
presentes en las respuestas y valen `null` cuando Telegram no los proporciona,
así que los clientes pueden contar con una estructura estable.

**Las fechas** son cadenas ISO-8601 con desfase horario más una marca de tiempo
Unix:

```json
{"string": "2026-10-20T10:00:00+00:00", "unix": 1792490400}
```

## Cabeceras de respuesta { #response-headers }

| Cabecera | Cuándo | Significado |
|---|---|---|
| `X-App-Node` | siempre | Nombre de host del servidor que respondió: útil detrás de un balanceador de carga |
| `X-Process-Time` | siempre | Tiempo empleado en la aplicación, p. ej. `3 ms` |
| `Cache-Control` | solicitudes con la cabecera `CF-RAY` (Cloudflare) | `public, max-age=10, stale-while-revalidate=10` (`normal`) o `public, max-age=120, stale-while-revalidate=240` (`aggressive`); ninguna con `off` |
| `X-RateLimit-Limit`, `X-RateLimit-Remaining` | límite de solicitudes activado | Cupo por IP para el minuto actual |
| `Retry-After` | respuestas `429` | Segundos de espera (siempre `60`) |

CORS está abierto (`Access-Control-Allow-Origin` refleja el origen de la
solicitud) salvo que se restrinja con
[`CORS_ALLOW_ORIGINS`](../configuration.md#cors_allow_origins).

## Actualidad de los datos { #freshness }

Las respuestas provienen de una caché que dura desde unos segundos hasta una
hora, según el endpoint y [`CACHE_MODE`](../configuration.md#cache_mode):

| Endpoint | `off` | `normal` | `aggressive` |
|---|---|---|---|
| `/v1/body`, `/v1/post`, `/v1/more` | siempre en vivo | 10 s | 120 s |
| `/v1/preview`, `/v1/previews` | [`PREVIEW_CACHE_TTL`](../configuration.md#preview_cache_ttl) (1 h) | igual | igual |
| `/v1/feed` | consulta [Feed](feed.md#freshness) | | |

Las solicitudes idénticas que llegan mientras se consulta t.me comparten esa
única solicitud al origen.

## Códigos de estado { #status-codes }

| Código | Significado |
|---|---|
| `200` | Éxito |
| `404` | El canal o la publicación no existe (o es privado) |
| `422` | Entrada no válida: consulta [Errores](errors.md#422-validation-errors) |
| `429` | Se superó el límite de solicitudes |
| `502` | t.me respondió con una página que el parser no pudo interpretar |
| `503` | t.me no está accesible o respondió con un error, o la función está desactivada |

Todos los cuerpos de error se describen en [Errores](errors.md).
