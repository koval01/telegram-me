# Modelos

Todos los modelos de respuesta, tal como se definen en
[`telegram_me/schemas`](https://github.com/koval01/telegram-me/tree/main/src/telegram_me/schemas).
Las mismas definiciones están disponibles como JSON Schema en `/openapi.json`.

Los campos opcionales siempre están presentes en las respuestas y valen `null`
cuando faltan (el [feed](feed.md) devuelve publicaciones sin procesar y, en
cambio, los omite).

## ChannelBody

Lo devuelve [`/v1/body`](channel-page.md).

| Campo | Tipo | Descripción |
|---|---|---|
| `channel` | [Channel](#channel) | Perfil |
| `content.posts` | [Post](#post)[] | Publicaciones de la página, de la más antigua a la más reciente |
| `meta` | [Meta](#meta) | Cursores de paginación |

## More

Lo devuelve [`/v1/more`](more.md): `posts` ([Post](#post)[]) y `meta`
([Meta](#meta)).

## Channel

| Campo | Tipo | Descripción |
|---|---|---|
| `username` | string | Nombre de usuario sin `@` |
| `title` | [ParsedAndRaw](#parsedandraw) | Nombre visible |
| `description` | [ParsedAndRaw](#parsedandraw) \| null | Biografía |
| `avatar` | URL \| null | Imagen de perfil |
| `counters.subscribers` | string | p. ej. `10.3M` |
| `counters.photos`, `videos`, `files`, `links` | string \| null | Contadores de multimedia que muestra Telegram |
| `labels` | string[] \| null | Insignias de la cabecera, p. ej. `verified` |

## Meta

| Campo | Tipo | Descripción |
|---|---|---|
| `offset.before` | integer \| null | Pásalo a `/v1/more/{channel}/before/{value}` para obtener publicaciones más antiguas |
| `offset.after` | integer \| null | Pásalo a `/v1/more/{channel}/after/{value}` para obtener publicaciones más recientes |

## Post

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | integer | ID de la publicación dentro del canal |
| `content` | [Content](#content) | Todo lo que contiene la publicación |
| `footer` | [Footer](#footer) | Visualizaciones, fecha, autor |
| `forwarded` | [Forwarded](#forwarded) \| null | Origen de un reenvío |
| `view` | string | Token de visualización opaco de Telegram |

### Content

| Campo | Tipo | Descripción |
|---|---|---|
| `text` | [Text](#text) \| null | Texto del mensaje |
| `media` | [MediaItem](#mediaitem)[] \| null | Fotos, vídeos, mensajes de voz, videomensajes redondos, stickers |
| `poll` | [Poll](#poll) \| null | Encuesta *(solo con el parser completo)* |
| `inline` | [Inline](#inline)[] \| null | Botones del teclado inline, todas las filas aplanadas en orden |
| `reply` | [Reply](#reply) \| null | Mensaje al que se responde *(solo con el parser completo)* |
| `preview_link` | [PreviewLink](#previewlink) \| null | Tarjeta de vista previa de enlace *(solo con el parser completo)* |
| `reacts` | [Reacts](#reacts)[] \| null | Reacciones *(solo con el parser completo)* |

Con [`PARSER_MODE=simplified`](../configuration.md#parser_mode) solo se
extraen `text` (sin entidades), `media` e `inline`.

### Text

| Campo | Tipo | Descripción |
|---|---|---|
| `string` | string | Texto plano, `\n` para los saltos de línea |
| `html` | string | Marcado original |
| `entities` | [TextEntity](#textentity)[] \| null | Formato *(solo con el parser completo)* |

### TextEntity

| Campo | Tipo | Descripción |
|---|---|---|
| `type` | enum | `bold`, `italic`, `underline`, `strikethrough`, `code`, `pre`, `spoiler`, `hashtag`, `url`, `text_link`, `emoji`, `animoji` |
| `offset` | integer | Inicio en `Text.string` (**no** se usa UTF-16: índices de cadenas de Python) |
| `length` | integer | Longitud del texto visible |
| `url` | URI \| null | Destino del enlace (`text_link`, `url`) o imagen del emoji (`emoji`, `animoji`) |
| `language` | string \| null | Reservado para los bloques `pre` |

### MediaItem

| Campo | Tipo | Descripción |
|---|---|---|
| `type` | enum | `image`, `video`, `gif`, `voice`, `roundvideo`, `sticker` |
| `url` | URL \| null | URL del archivo; `null` cuando Telegram no la expone |
| `thumb` | URL \| null | Miniatura (vídeo, videomensaje redondo, sticker) |
| `duration` | `{formatted, raw}` \| null | `formatted` tal como se muestra (`1:15`), `raw` en segundos |
| `waves` | string \| null | Forma de onda codificada de un mensaje de voz |
| `available` | boolean \| null | `false` cuando el archivo es demasiado grande para que t.me lo sirva |

Un vídeo sin duración se devuelve como `gif`.

### Poll

| Campo | Tipo | Descripción |
|---|---|---|
| `question` | string | Pregunta |
| `type` | string \| null | Tal como lo muestra Telegram, p. ej. `Anonymous Poll` |
| `votes` | string \| null | Contador de votos tal como se muestra |
| `options[]` | `{name: string, percent: integer}` | Respuestas con su porcentaje |

### Inline

| Campo | Tipo | Descripción |
|---|---|---|
| `title` | string | Texto del botón |
| `url` | URI | Destino (`https://…`, `tg://…`) |

### Reply

| Campo | Tipo | Descripción |
|---|---|---|
| `to_message` | integer | ID de la publicación a la que se responde |
| `name` | [ParsedAndRaw](#parsedandraw) | Su autor |
| `text` | [ParsedAndRaw](#parsedandraw) | Su texto (fragmento) |
| `cover` | URI \| null | Su miniatura |

### PreviewLink

| Campo | Tipo | Descripción |
|---|---|---|
| `url` | URL | Página enlazada |
| `site_name`, `title` | string \| null | Cabecera de la tarjeta |
| `description` | [ParsedAndRaw](#parsedandraw) \| null | Texto de la tarjeta |
| `thumb` | URL \| null | Imagen de la tarjeta |

### Reacts

| Campo | Tipo | Descripción |
|---|---|---|
| `type` | enum | `emoji`, `custom_emoji`, `telegram_stars` |
| `count` | string | Contador tal como se muestra (`14.2K`) |
| `emoji` | string \| null | El emoji (`⭐` para las Stars) |
| `emoji_id` | string \| null | ID del emoji personalizado |
| `emoji_image` | URL \| null | Sprite del emoji |

### Footer

| Campo | Tipo | Descripción |
|---|---|---|
| `views` | string \| null | Contador de visualizaciones |
| `edited` | boolean | La publicación se editó |
| `author` | [ParsedAndRaw](#parsedandraw) \| null | Firma, cuando el canal firma las publicaciones |
| `date` | `{string, unix}` | Momento de publicación |

### Forwarded

| Campo | Tipo | Descripción |
|---|---|---|
| `name` | [ParsedAndRaw](#parsedandraw) | Canal o usuario de origen; cadenas vacías cuando el remitente oculta su nombre |
| `url` | URL \| null | Enlace al origen |
| `author` | [ParsedAndRaw](#parsedandraw) \| null | Firma junto al origen (añadido en 2.0) |

## Preview

Lo devuelve [`/v1/preview`](previews.md): `{"channel": PreviewChannel}`.

| Campo | Tipo | Descripción |
|---|---|---|
| `title` | string | Nombre |
| `subscribers` | string | Tal como se muestra, p. ej. `10 281 023 subscribers` |
| `description` | string \| null | Biografía en texto plano |
| `avatar` | URL \| null | Imagen |
| `is_verified` | boolean | Insignia de verificación |

`/v1/previews` devuelve `{username: Preview}`.

## FeedResponse

`{"result": [ … ]}`: consulta [Feed](feed.md#response).

## ParsedAndRaw

`{"string": string, "html": string}`: texto plano y marcado original.
