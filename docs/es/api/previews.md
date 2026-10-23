# Vistas previas de canales

Las vistas previas son resúmenes breves de canales tomados de la página de
inicio `https://t.me/<channel>`: más baratas que una página de canal completa y
guardadas en caché una hora por defecto
([`PREVIEW_CACHE_TTL`](../configuration.md#preview_cache_ttl)).

Ambos endpoints responden `503` cuando las vistas previas están desactivadas
con [`ENABLE_PREVIEWS=false`](../configuration.md#enable_previews).

## `GET /v1/preview/{channel}`

```bash
curl http://localhost:8000/v1/preview/durov
```

```json
{
  "channel": {
    "title": "Pavel Durov",
    "subscribers": "10 281 023 subscribers",
    "description": "Founder of Telegram.",
    "avatar": "https://cdn4.telesco.pe/file/TUoXCel1JUtqk….jpg",
    "is_verified": true
  }
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `title` | string | Nombre del canal |
| `subscribers` | string | Tal como lo muestra Telegram, incluida la palabra «subscribers» |
| `description` | string \| null | Texto plano; los enlaces se reducen a su texto y `<br>` a `\n` |
| `avatar` | URL \| null | `null` cuando Telegram incrusta la imagen en línea |
| `is_verified` | boolean | Insignia de verificación |

Se devuelve `404` para los canales que no existen, los canales privados, los
usuarios y los bots. Los canales desconocidos se recuerdan durante 60
segundos, así que las consultas repetidas no llegan a t.me.

## `POST /v1/previews`

Vistas previas de hasta 100 canales en una sola llamada, resueltas en
paralelo.

```bash
curl -X POST http://localhost:8000/v1/previews \
  -H 'content-type: application/json' \
  -d '{"channels": ["durov", "telegram", "nosuchchannel123"]}'
```

```json
{
  "durov":    {"channel": {"title": "Pavel Durov", "subscribers": "10 281 023 subscribers", "description": "Founder of Telegram.", "avatar": "https://…", "is_verified": true}},
  "telegram": {"channel": {"title": "Telegram News", "subscribers": "9 257 928 subscribers", "description": "The official Telegram on Telegram. Much recursion. …", "avatar": "https://…", "is_verified": true}}
}
```

- Las claves son los nombres de usuario tal como se enviaron; los duplicados se
  resuelven una sola vez.
- Los canales que no existen se **omiten** del resultado.
- Si todos los canales fallan por otro motivo (por ejemplo, t.me no responde),
  la solicitud falla con ese error (`503`).

!!! note "Cambió en 2.0"
    1.x respondía `404` a toda la solicitud cuando faltaba un solo canal. 2.0
    devuelve los canales que pudo resolver.

Cuerpo de la solicitud: `{"channels": [string, …]}` con entre 1 y 100 nombres
de usuario válidos; un array JSON suelto se rechaza con `422`.
