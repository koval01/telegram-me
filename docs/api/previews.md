# Channel previews

Previews are short channel summaries taken from the landing page
`https://t.me/<channel>` — cheaper than a full channel page and cached for an
hour by default ([`PREVIEW_CACHE_TTL`](../configuration.md#preview_cache_ttl)).

Both endpoints answer `503` when previews are disabled with
[`ENABLE_PREVIEWS=false`](../configuration.md#enable_previews).

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

| Field | Type | Notes |
|---|---|---|
| `title` | string | Channel name |
| `subscribers` | string | As displayed by Telegram, including the word "subscribers" |
| `description` | string \| null | Plain text; links are reduced to their text, `<br>` to `\n` |
| `avatar` | URL \| null | `null` when Telegram embeds the image inline |
| `is_verified` | boolean | Verified badge |

`404` is returned for channels that do not exist, private channels, users and
bots. Unknown channels are remembered for 60 seconds, so repeated lookups do
not reach t.me.

## `POST /v1/previews`

Previews of up to 100 channels in one call, resolved in parallel.

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

- Keys are the usernames as sent; duplicates are resolved once.
- Channels that do not exist are **omitted** from the result.
- If every channel fails for another reason (for example t.me is down) the
  request fails with that error (`503`).

!!! note "Changed in 2.0"
    1.x answered `404` for the whole request when any single channel was
    missing. 2.0 returns the channels it could resolve.

Request body: `{"channels": [string, …]}` with 1–100 valid usernames; a bare
JSON array is rejected with `422`.
