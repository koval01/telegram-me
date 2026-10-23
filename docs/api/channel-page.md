# Channel page

```http
GET /v1/body/{channel}?position={id}
```

Returns the channel profile, a page of posts (about 20, oldest first) and the
cursors to load more. It mirrors `https://t.me/s/{channel}` and, with
`position`, `https://t.me/s/{channel}/{position}`.

## Parameters

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `channel` | path | string | yes | Channel username, e.g. `durov` |
| `position` | query | integer 1…10 000 000 | no | Open the page around this post instead of the latest posts |

## Response

A [`ChannelBody`](models.md#channelbody):

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

### Fields worth knowing

- `channel.counters` contains only what Telegram shows; unknown counters are
  `null`.
- `channel.labels` lists header badges such as `verified`; `null` when there
  are none.
- `content.posts[].view` is Telegram's opaque view token for the post.
- `meta.offset.before` / `after` are post IDs to pass to
  [`/v1/more`](more.md). `after` is `null` on the latest page.

Each post is described in detail in [Models → Post](models.md#post).

## Errors

| Status | When |
|---|---|
| `404` | The channel does not exist, is private, or is a user/bot rather than a channel |
| `422` | Invalid `channel` or `position` |
| `502` | The page could not be interpreted |
| `503` | t.me unreachable or answered with an error |

A single post with unexpected markup is skipped (and logged) instead of
failing the whole page.

## Examples

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
