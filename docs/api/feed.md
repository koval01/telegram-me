# Feed

```http
POST /v1/feed
```

Merges the latest posts of up to 100 channels into one list, ranked by a
score that combines engagement, content richness and freshness.

!!! warning "Disabled by default"
    The feed is the heaviest endpoint: it needs a page of every requested
    channel. It answers `503` until enabled with
    [`ENABLE_FEED=true`](../configuration.md#enable_feed). Run it with Redis
    (`REDIS_URL`) when you have more than one worker or server, so all of them
    share the page cache and the popularity ranking.

## Request

```json
{"channels": ["durov", "telegram", "stranaua"]}
```

1–100 valid usernames. Duplicates are ignored. Invalid input is answered with
`422` even while the feed is disabled.

## Response

```json
{
  "result": [
    {
      "id": 552,
      "content": { … },
      "footer": { … },
      "view": "…",
      "channel": {
        "username": "durov",
        "title": {"string": "Pavel Durov", "html": "Pavel Durov"},
        "description": { … },
        "avatar": "https://…",
        "counters": {"subscribers": "10.3M", "photos": "102", "videos": "47", "links": "200"},
        "labels": ["verified"]
      },
      "_score": 0.1688
    }
  ]
}
```

Each item is a post in its **raw parsed form** (fields Telegram did not
provide are omitted rather than `null`), plus:

- `channel` — the profile of the channel the post belongs to;
- `_score` — the ranking score in `[0, 10]`, highest first.

Posts are de-duplicated by `(channel, id)`. Channels that cannot be loaded
(unknown, t.me errors) are skipped.

## Freshness

The feed uses stale-while-revalidate caching per channel page:

| Age of the cached page | What happens |
|---|---|
| < [`FEED_FRESH_TTL`](../configuration.md#feed_fresh_ttl) (60 s) | used as is |
| < [`FEED_STALE_TTL`](../configuration.md#feed_stale_ttl) (15 min) | used immediately; a background refresh replaces it |
| older / missing | fetched now — all missing channels in parallel |

A background **refresher** keeps the channels people actually ask for warm:
the more often a channel appears in feed requests, the more often it is
refreshed (every 30 s for the most popular, up to every 10 min). Most feed
requests therefore never wait for t.me. Details:
[Internals → Feed](../internals/feed.md).

## Ranking

```text
score = min(10, (0.4·engagement + 0.2·content + 0.02·edited) · (freshness + 0.1))
```

- **engagement** — views, positive minus negative reactions and comments,
  each relative to the channel's subscribers and capped;
- **content** — bonus for photos, videos, GIFs and polls (capped at 10 each)
  and for longer texts;
- **freshness** — 1.0 for a new post, 0.8 after a day, decaying exponentially
  over the week and slowly afterwards;
- **edited** — a small bonus for edited posts.

The exact formula is documented in [Internals → Feed](../internals/feed.md#scoring).
With `PARSER_MODE=simplified` reactions are not parsed and do not contribute.
