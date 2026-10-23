# Single post

```http
GET /v1/post/{channel}/{identifier}
```

Returns one [`Post`](models.md#post). Internally this reads the same page as
`/v1/body/{channel}?position={identifier}`, so asking for a post and then for
its surrounding page costs a single request to t.me.

## Parameters

| Name | In | Type | Description |
|---|---|---|---|
| `channel` | path | string | Channel username |
| `identifier` | path | integer 1…10 000 000 | Post ID (the number in `t.me/<channel>/<id>`) |

## Example

`GET /v1/post/nebudetgg/6209` — a post with an inline keyboard button:

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
  "forwarded": { … },
  "view": "…"
}
```

## Errors

| Status | When |
|---|---|
| `404` | The channel does not exist, or the post is not on the page (deleted, never existed) |
| `422` | Invalid parameters |
| `502` | The post exists but could not be interpreted |
| `503` | t.me unreachable |
