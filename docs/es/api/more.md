# Paginación

```http
GET /v1/more/{channel}/{direction}/{position}
```

Carga las publicaciones más antiguas (`before`) o más recientes (`after`) que
`position`, como al desplazarse por el canal en t.me. La publicación de
referencia no se incluye.

## Parámetros { #parameters }

| Nombre | En | Tipo | Descripción |
|---|---|---|---|
| `channel` | path | string | Nombre de usuario del canal |
| `direction` | path | `before` \| `after` | `before` = publicaciones más antiguas, `after` = más recientes |
| `position` | path | integer 1…10 000 000 | ID de la publicación de referencia |

## Respuesta { #response }

Un [`More`](models.md#more): `posts` (con la misma forma que en la página del
canal) y `meta.offset` con los siguientes cursores. Por ejemplo,
`GET /v1/more/durov/before/531` devuelve las publicaciones 510…529:

```json
{
  "posts": [ { "id": 510, "content": { … }, "footer": { … }, "forwarded": null, "view": "…" }, … ],
  "meta": {"offset": {"before": 510, "after": null}}
}
```

## Recorrer un canal entero { #walking-a-whole-channel }

```python
import httpx

api = "http://localhost:8000/v1"
page = httpx.get(f"{api}/body/durov").json()
posts = page["content"]["posts"]
cursor = page["meta"]["offset"]["before"]

while cursor:
    batch = httpx.get(f"{api}/more/durov/before/{cursor}").json()
    if not batch["posts"]:
        break
    posts = batch["posts"] + posts
    cursor = batch["meta"]["offset"]["before"]

print(len(posts), "posts")
```

!!! tip "Con moderación"
    Cada página que no está en caché es una solicitud a t.me. Recorrer canales
    grandes rápidamente puede hacer que Telegram limite la frecuencia de
    solicitudes de la IP del servidor; añade una pausa entre páginas.

## Errores { #errors }

`404` para canales desconocidos, `422` para parámetros no válidos, `502`/`503`
como se describe en [Errores](errors.md).
