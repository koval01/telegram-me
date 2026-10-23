# Una publicación

```http
GET /v1/post/{channel}/{identifier}
```

Devuelve un [`Post`](models.md#post). Internamente lee la misma página que
`/v1/body/{channel}?position={identifier}`, así que pedir una publicación y
después la página que la rodea cuesta una sola solicitud a t.me.

## Parámetros { #parameters }

| Nombre | En | Tipo | Descripción |
|---|---|---|---|
| `channel` | path | string | Nombre de usuario del canal |
| `identifier` | path | integer 1…10 000 000 | ID de la publicación (el número de `t.me/<channel>/<id>`) |

## Ejemplo { #example }

`GET /v1/post/nebudetgg/6209`: una publicación con un botón de teclado inline:

```json
{
  "id": 6209,
  "content": {
    "text": {"string": "…", "html": "…", "entities": [ … ]},
    "media": [ … ],
    "inline": [
      {
        "title": "Участвовать (3145)",
        "url": "https://t.me/Random/JoinLot?startapp=2154773G7806d14&startApp=2154773G7806d14"
      }
    ],
    "poll": null,
    "reply": null,
    "preview_link": null,
    "reacts": [ … ]
  },
  "footer": {"views": "207K", "edited": false, "author": null, "date": {"string": "2026-05-12T09:39:25+00:00", "unix": 1778578765}},
  "forwarded": {
    "name": {"string": "", "html": ""},
    "url": null,
    "author": {"string": "RandomGodBot⚡️ [Рандомайзер]", "html": "RandomGodBot<i class=\"emoji\" …><b>⚡️</b></i> [Рандомайзер]"}
  },
  "view": "…"
}
```

## Errores { #errors }

| Estado | Cuándo |
|---|---|
| `404` | El canal no existe, o la publicación no está en la página (eliminada o inexistente) |
| `422` | Parámetros no válidos |
| `502` | La publicación existe, pero no se pudo interpretar |
| `503` | t.me no está accesible |
