# Pagination

```http
GET /v1/more/{channel}/{direction}/{position}
```

Loads the posts older (`before`) or newer (`after`) than `position`, like
scrolling the channel on t.me. The anchor post itself is not included.

## Parameters

| Name | In | Type | Description |
|---|---|---|---|
| `channel` | path | string | Channel username |
| `direction` | path | `before` \| `after` | `before` = older posts, `after` = newer posts |
| `position` | path | integer 1…10 000 000 | Anchor post ID |

## Response

A [`More`](models.md#more): `posts` (same shape as in the channel page) and
`meta.offset` with the next cursors. For example `GET /v1/more/durov/before/531`
returns posts 510…529:

```json
{
  "posts": [ { "id": 510, "content": { … }, "footer": { … }, "forwarded": null, "view": "…" }, … ],
  "meta": {"offset": {"before": 510, "after": null}}
}
```

## Walking a whole channel

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

!!! tip "Be gentle"
    Every uncached page is a request to t.me. Walking large channels quickly
    may get the server's IP rate-limited by Telegram; add a delay between
    pages.

## Errors

`404` for unknown channels, `422` for invalid parameters, `502`/`503` as
described in [Errors](errors.md).
