# Página del canal

```http
GET /v1/body/{channel}?position={id}
```

Devuelve el perfil del canal, una página de publicaciones (unas 20, de la más
antigua a la más reciente) y los cursores para cargar más. Equivale a
`https://t.me/s/{channel}` y, con `position`,
a `https://t.me/s/{channel}/{position}`.

## Parámetros { #parameters }

| Nombre | En | Tipo | Obligatorio | Descripción |
|---|---|---|---|---|
| `channel` | path | string | sí | Nombre de usuario del canal, p. ej. `durov` |
| `position` | query | integer 1…10 000 000 | no | Abre la página alrededor de esta publicación en lugar de las últimas publicaciones |

## Respuesta { #response }

Un [`ChannelBody`](models.md#channelbody):

```json
{
  "channel": {
    "username": "durov",
    "title": {"string": "Pavel Durov", "html": "Pavel Durov"},
    "description": {"string": "Founder of Telegram.", "html": "Founder of Telegram."},
    "avatar": "https://cdn4.telesco.pe/file/TUoXCel1JUtqk….jpg",
    "counters": {
      "subscribers": "10.3M",
      "photos": "102",
      "videos": "47",
      "files": null,
      "links": "200"
    },
    "labels": ["verified"]
  },
  "content": {
    "posts": [
      {
        "id": 552,
        "content": {
          "text": {
            "string": "✨ Designers × AI\n\n🆕 The mockup era is over …",
            "html": "<tg-emoji emoji-id=\"5325547803936572038\"><i class=\"emoji\" …><b>✨</b></i></tg-emoji> <b>Designers × AI<br/><br/></b> …",
            "entities": [
              {"offset": 0, "length": 1, "type": "animoji", "url": "https://telegram.org/img/emoji/40/E29CA8.png", "language": null},
              {"offset": 2, "length": 16, "type": "bold", "url": null, "language": null}
            ]
          },
          "media": [
            {
              "url": null,
              "thumb": "https://cdn4.telesco.pe/file/Sugu_UFeS….jpg",
              "waves": null,
              "duration": {"formatted": "1:15", "raw": 75},
              "type": "video",
              "available": false
            }
          ],
          "poll": null,
          "inline": null,
          "reply": null,
          "preview_link": null,
          "reacts": [
            {"count": "6.07K", "type": "telegram_stars", "emoji": "⭐", "emoji_id": null, "emoji_image": null},
            {"count": "14.2K", "type": "custom_emoji", "emoji": null, "emoji_id": "5936157098181135162", "emoji_image": null}
          ]
        },
        "footer": {
          "views": "1.34M",
          "edited": false,
          "author": {"string": "Pavel Durov", "html": "Pavel Durov"},
          "date": {"string": "2026-10-11T14:52:40+00:00", "unix": 1791730360}
        },
        "forwarded": null,
        "view": "eyJjIjotMTAwNjUwMzEyMiwicCI6NTUyLCJ0IjoxNzky…"
      }
    ]
  },
  "meta": {"offset": {"before": 531, "after": null}}
}
```

### Campos que conviene conocer { #fields-worth-knowing }

- `channel.counters` contiene solo lo que muestra Telegram; los contadores
  desconocidos valen `null`.
- `channel.labels` enumera las insignias de la cabecera, como `verified`;
  `null` cuando no hay ninguna.
- `content.posts[].view` es el token de visualización opaco de Telegram para la
  publicación.
- `meta.offset.before` / `after` son ID de publicaciones que se pasan a
  [`/v1/more`](more.md). `after` es `null` en la página más reciente.

Cada publicación se describe en detalle en
[Modelos → Post](models.md#post).

## Errores { #errors }

| Estado | Cuándo |
|---|---|
| `404` | El canal no existe, es privado o es un usuario/bot en lugar de un canal |
| `422` | `channel` o `position` no válidos |
| `502` | No se pudo interpretar la página |
| `503` | t.me no está accesible o respondió con un error |

Una publicación con un marcado inesperado se omite (y se registra en el log)
en lugar de hacer fallar toda la página.

## Ejemplos { #examples }

=== "curl"

    ```bash
    curl "http://localhost:8000/v1/body/durov"
    curl "http://localhost:8000/v1/body/stranaua?position=252483"
    ```

=== "Python"

    ```python
    import httpx

    page = httpx.get("http://localhost:8000/v1/body/durov").json()
    for post in page["content"]["posts"]:
        text = (post["content"].get("text") or {}).get("string", "")
        print(post["id"], post["footer"]["views"], text[:60])
    ```

=== "JavaScript"

    ```javascript
    const page = await fetch("http://localhost:8000/v1/body/durov").then((r) => r.json());
    console.log(page.channel.title.string, page.content.posts.length);
    ```
