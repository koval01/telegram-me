# Análisis de t.me

## Páginas { #pages }

| Página | URL | Parser |
|---|---|---|
| Feed del canal | `GET https://t.me/s/{channel}[/{position}]` | `parse_channel_page` |
| Desplazamiento infinito | `POST https://t.me/s/{channel}?before=…` / `?after=…` con `X-Requested-With: XMLHttpRequest`: devuelve una cadena JSON con un fragmento HTML | `parse_more_page` |
| Página de inicio | `GET https://t.me/{channel}` | `parse_preview_page` |

Las solicitudes se envían con las cabeceras de un Chrome móvil. Las
redirecciones no se siguen: un canal desconocido redirige `/s/{channel}` a
`/{channel}`, lo que se devuelve entonces como `404`.

## Extracción { #extraction }

El análisis está implementado en Rust
([`rust/telegram-me-native/src/parse`](https://github.com/koval01/telegram-me/tree/main/rust/telegram-me-native/src/parse))
y se ejecuta con el GIL liberado: el HTML se analiza una vez con html5ever (el
parser conforme a WHATWG del proyecto Servo) y se recorre con selectores CSS.
Python solo decodifica el JSON resultante (pydantic-core) y registra en el log
las publicaciones omitidas.

La versión en Rust reproduce byte a byte el parser original en
Python/selectolax: los selectores coinciden tanto con el propio nodo como con
sus descendientes, el texto se concatena exactamente como el `text()` de
lexbor, el HTML se vuelve a serializar con las reglas de escape y el orden de
atributos de lexbor, y las etiquetas de formato que se desenvuelven para el
texto plano siguen desenvueltas en las lecturas posteriores del mismo
subárbol, como con el `unwrap_tags` in situ de selectolax. Los snapshots del
contrato fijan el resultado.

Por publicación (`.tgme_widget_message_wrap > .tgme_widget_message`):

| Campo | Fuente |
|---|---|
| `id` | `data-post="<channel>/<id>"` |
| `text` | `.tgme_widget_message_text`: `html` es el marcado interior; `string`, el texto con las etiquetas de formato desenvueltas y `<br>` como `\n` |
| `entities` | expresiones regulares sobre el marcado del texto (negrita, cursiva, subrayado, tachado, código, spoiler, hashtags, enlaces, emoji, emoji personalizados); las alternativas sin look-ahead se ejecutan en el motor DFA de `regex`, y las tres con look-ahead se comprueban de forma barata y solo recurren a `fancy-regex` cuando hace falta |
| `media` | contenedores de fotos, reproductores de vídeo y de videomensajes redondos, reproductores de voz, stickers (variantes `picture`, `i`, `video`), imágenes de vistas previas de enlaces |
| `poll` | pregunta, tipo, opciones con porcentajes y votantes de `.tgme_widget_message_poll` |
| `inline` | cada `.tgme_widget_message_inline_button` del teclado inline |
| `reply` | `.tgme_widget_message_reply`: autor, texto, miniatura, publicación de destino a partir de su enlace |
| `preview_link` | `.tgme_widget_message_link_preview` |
| `reacts` | `.tgme_reaction`: Telegram Stars, emoji estándar (con la URL del sprite), emoji personalizados (`emoji-id`) |
| `footer` | `time[datetime]`, visualizaciones, marca de «editado», firma del autor |
| `forwarded` | `.tgme_widget_message_forwarded_from_name` y `_author` |

## Robustez { #robustness }

- Cada publicación se analiza por separado. Si algo de una publicación es
  inesperado, esa publicación se registra en el log
  (`Skipping unparsable post <channel>/<id>`) y se deja fuera; se devuelve el
  resto de la página.
- Tras el análisis, la página se valida contra los modelos de respuesta. Las
  publicaciones que no validan se descartan del mismo modo, así que una
  publicación rara nunca convierte toda la respuesta en un error.
- Solo cuando la página en su conjunto no tiene sentido se devuelve un `502`.

## Cuando Telegram cambia su marcado { #when-telegram-changes-its-markup }

1. Reprodúcelo con `uv run python scripts/record_fixtures.py <channel>` para
   grabar el nuevo marcado.
2. Corrige el parser en `rust/telegram-me-native/src/parse/` y ejecuta
   `make rust` y `make native`.
3. Ejecuta `make snapshots` y revisa el diff de los snapshots: muestra
   exactamente qué respuestas cambiaron.
