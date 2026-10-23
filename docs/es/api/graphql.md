# GraphQL

```http
POST /graphql
```

Una API GraphQL junto a la API REST `/v1`, para clientes que quieren elegir los
campos que reciben o combinar varias solicitudes en un solo viaje de ida y
vuelta. Se apoya en los mismos servicios que REST: la misma caché, la misma
agrupación de solicitudes, validación y feature flags, y los mismos datos.

Está activada por defecto; desactívala con
[`ENABLE_GRAPHQL=false`](../configuration.md#enable_graphql), tras lo cual
`/graphql` responde `404`.

## Endpoint { #endpoint }

| Método | Qué |
|---|---|
| `POST /graphql` | `{"query": …, "variables": {…}, "operationName": …}` como JSON, o un **array** JSON de tales objetos (un lote, consulta [Límites](#limits)) |
| `GET /graphql` | En un navegador: el IDE GraphiQL. Con `?query=…&variables=…`: ejecuta una consulta |

GraphiQL, las consultas `GET` y la introspección del esquema están disponibles
mientras la documentación esté activada;
[`DISABLE_DOCS=true`](../configuration.md#disable_docs) desactiva las tres
(`POST` sigue funcionando).

```bash
curl http://localhost:8000/graphql \
  -H 'content-type: application/json' \
  -d '{"query": "{ body(channel: \"durov\") { channel { title { string } } } }"}'
```

```json
{"data": {"body": {"channel": {"title": {"string": "Pavel Durov"}}}}}
```

## Esquema { #schema }

Cada campo raíz refleja un endpoint REST y devuelve los mismos datos:

| Campo | Devuelve | Equivalente REST |
|---|---|---|
| `body(channel, position)` | `ChannelBody` | [`GET /v1/body/{channel}`](channel-page.md) |
| `more(channel, direction, position)` | `More` | [`GET /v1/more/{channel}/{direction}/{position}`](more.md) |
| `post(channel, id)` | `Post` | [`GET /v1/post/{channel}/{identifier}`](post.md) |
| `preview(channel)` | `Preview` | [`GET /v1/preview/{channel}`](previews.md) |
| `previews(channels)` | `[Preview!]` | [`POST /v1/previews`](previews.md) |
| `feed(channels)` | `[FeedPost!]` | [`POST /v1/feed`](feed.md) |

**Los nombres de los campos son las claves JSON de REST** (`preview_link`,
`to_message`, `is_verified`, …), así que un resultado de GraphQL es
exactamente la respuesta REST sin los campos que no pediste. Por eso los
[modelos de datos](models.md) describen ambas API. Las diferencias:

- `preview` y `previews` devuelven objetos `Preview` con un campo adicional
  `username`; `previews` es una lista (en el orden de la solicitud, sin los
  canales desconocidos) en lugar de un mapa indexado por nombre de usuario.
- Los elementos del feed son `FeedPost`: un `Post` más `channel`, `score`,
  `duplicates` y `topic` (en REST: `_score`, `_duplicates`, `_topic`).
  `duplicates` es una lista vacía cuando no se agrupó nada en la publicación.
  Los campos ausentes de los datos sin procesar del feed valen `null`.
- Los conjuntos fijos de cadenas son enums cuyos valores son las cadenas de
  REST: `MediaType` (`image`, `video`, …), `EntityType`, `ReactionType` y
  `Direction` (`after`, `before`).
- `Date.unix` es un escalar `Long`: un entero JSON simple que puede superar los
  32 bits.

Los campos raíz admiten nulos: cuando uno falla vale `null` con una entrada en
`errors`, y los demás campos de la operación siguen devolviendo datos.

??? abstract "Esquema completo (SDL)"

    ```graphql
    --8<-- "tests/snapshots/graphql/schema.graphql"
    ```

## Ejemplos { #examples }

Los ID de las últimas publicaciones, sus visualizaciones y el cursor de
paginación de un canal:

```graphql
{
  body(channel: "durov") {
    content { posts { id footer { views date { unix } } } }
    meta { offset { before } }
  }
}
```

La página siguiente, con variables:

```graphql
query Older($channel: String!, $before: Int!) {
  more(channel: $channel, direction: before, position: $before) {
    posts { id content { text { string } } }
    meta { offset { before } }
  }
}
```

```json
{"channel": "durov", "before": 531}
```

Varios canales en una sola solicitud, con alias y un fragmento:

```graphql
{
  durov: body(channel: "durov") { ...Latest }
  telegram: body(channel: "telegram") { ...Latest }
  previews(channels: ["durov", "telegram"]) {
    username
    channel { title subscribers is_verified }
  }
}

fragment Latest on ChannelBody {
  channel { title { string } }
  content { posts { id content { media { type url thumb } } } }
}
```

Un feed clasificado (requiere
[`ENABLE_FEED`](../configuration.md#enable_feed)):

```graphql
{
  feed(channels: ["durov", "telegram", "stranaua"]) {
    id
    score
    channel { username }
    content { text { string } }
  }
}
```

Un lote de dos operaciones en una sola solicitud HTTP:

```json
[
  {"query": "{ post(channel: \"durov\", id: 531) { view } }"},
  {"query": "{ preview(channel: \"telegram\") { channel { title } } }"}
]
```

La respuesta es un array con un resultado por operación, en orden.

## Eficiencia { #efficiency }

- Cada página se descarga como mucho una vez por solicitud HTTP: los campos
  raíz iguales (alias, fragmentos repetidos, las operaciones de un lote)
  comparten un resultado, y los campos que necesitan la misma página al mismo
  tiempo (`body(position: 531)` y `post(id: 531)`) comparten una solicitud al
  origen.
- Los campos raíz se resuelven de forma concurrente; `previews` y `feed`
  descargan sus canales en paralelo (siempre dentro del límite de
  [`TELEGRAM_MAX_CONCURRENCY`](../configuration.md#telegram_max_concurrency)).
- Las respuestas provienen de las mismas cachés que REST (consulta
  [Actualidad de los datos](index.md#freshness)), así que combinar ambas API no
  duplica la carga sobre t.me. Las consultas `GET` que llegan desde Cloudflare
  reciben el mismo `Cache-Control` que las respuestas REST.

## Límites { #limits }

Cada operación se comprueba antes de descargar nada. Todos los límites son
configurables:

| Ajuste | Por defecto | Rechaza |
|---|---|---|
| [`GRAPHQL_MAX_DEPTH`](../configuration.md#graphql_max_depth) | `10` | Selecciones anidadas a mayor profundidad que esta por debajo de un campo raíz (campo raíz = 0, salvo la introspección) |
| [`GRAPHQL_MAX_COMPLEXITY`](../configuration.md#graphql_max_complexity) | `1500` | Operaciones cuyo coste supera este valor |
| [`GRAPHQL_MAX_ALIASES`](../configuration.md#graphql_max_aliases) | `15` | Documentos con más alias |
| [`GRAPHQL_MAX_TOKENS`](../configuration.md#graphql_max_tokens) | `2000` | Documentos con más tokens que este valor |
| [`GRAPHQL_MAX_BATCH_SIZE`](../configuration.md#graphql_max_batch_size) | `10` | Lotes con más operaciones (`400 Too many operations`) |

**El coste** es 1 por cada campo seleccionado (con los fragmentos expandidos)
más 10 por cada canal que un campo raíz puede descargar: 10 para `body`,
`more`, `post` y `preview`, y 10 × el número de canales para `previews` y
`feed` (la longitud real de la lista, también cuando se pasa como variable). Un
`feed` sobre el máximo de 100 canales cuesta unos 1 000 + sus campos, así que
cabe en el límite por defecto; dos de ellos no.

Los límites se aplican a cada operación de un lote. El límite de solicitudes
([`ENABLE_RATE_LIMIT`](../configuration.md#enable_rate_limit)) cuenta
solicitudes HTTP, así que un lote cuenta una sola vez.

## Errores { #errors }

Los errores de GraphQL se devuelven con HTTP `200` en la lista `errors`, cada
uno con un `extensions.code` estable:

```json
{
  "data": {"body": null},
  "errors": [
    {
      "message": "Requested Telegram resource was not found",
      "locations": [{"line": 1, "column": 3}],
      "path": ["body"],
      "extensions": {"code": "NOT_FOUND"}
    }
  ]
}
```

| `extensions.code` | Estado REST | Causa |
|---|---|---|
| `BAD_INPUT` | `422` | Nombre de usuario no válido, ID/posición de publicación fuera de `1..10000000`, o no entre 1 y 100 canales |
| `NOT_FOUND` | `404` | El canal o la publicación no existe (o es privado) |
| `BAD_GATEWAY` | `502` | t.me respondió con una página que el parser no pudo interpretar |
| `UNAVAILABLE` | `503` | t.me no está accesible o respondió con un error |
| `FEATURE_DISABLED` | `503` | `preview`/`previews` con `ENABLE_PREVIEWS=false`, `feed` con `ENABLE_FEED=false` |
| `QUERY_TOO_COMPLEX` | — | La operación cuesta más que `GRAPHQL_MAX_COMPLEXITY` |
| `GRAPHQL_PARSE_FAILED` | — | Error de sintaxis, o más de `GRAPHQL_MAX_TOKENS` tokens |
| `GRAPHQL_VALIDATION_FAILED` | — | Campo o argumento desconocido, tipo incorrecto, límite de profundidad/alias, introspección mientras está desactivada |
| `INTERNAL_SERVER_ERROR` | `500` | Error inesperado del servidor; el mensaje se enmascara y los detalles se registran en el log |

Igual que en REST, la entrada se valida antes de comprobar las feature flags,
así que los argumentos no válidos dan `BAD_INPUT` incluso mientras una función
está desactivada. Los errores de las filas `BAD_INPUT`…`FEATURE_DISABLED` son
errores de ejecución: solo anulan su propio campo. Los demás se producen antes
de la ejecución y devuelven `"data": null`.

Las solicitudes que no son GraphQL en absoluto se rechazan con un estado HTTP:
`400` para un cuerpo que no es JSON, que no tiene `query` o que es un lote
demasiado grande; `400` para las consultas `GET` mientras la documentación
está desactivada; `429` cuando se aplica el límite de solicitudes.
