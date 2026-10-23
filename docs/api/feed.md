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
- `_score` — the ranking score in `[0, 10]`;
- `_duplicates` *(optional)* — the same story posted by other channels,
  collapsed into this post: `[{"channel": "…", "id": 123}, …]`;
- `_topic` *(optional)* — the subject this post shares with other posts in
  the feed: `{"id": 3, "keywords": ["starlink", "ограничения", …]}`.

The order is the feed order. It is mostly, but not strictly, by `_score`:
the ranker interleaves channels and subjects (see Ranking below).
Channels that cannot be loaded (unknown, t.me errors) are skipped.

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

Ranking runs natively in Rust and combines:

- **engagement relative to the channel** — views adjusted for the post's age
  and compared with the channel's other posts, so small channels' standout
  posts compete with large channels;
- **reactions**, **content richness** and **audience size**;
- **freshness** with a half-life of
  [`FEED_HALF_LIFE_HOURS`](../configuration.md#feed_half_life_hours) (24 h);
- **duplicate collapsing** across channels;
- **diversity**: fewer runs of one channel or one subject
  ([`FEED_DIVERSITY`](../configuration.md#feed_diversity));
- **topics** inferred from the texts.

The full algorithm is in [Internals → Feed](../internals/feed.md#ranking).
With `PARSER_MODE=simplified` reactions are not parsed and do not contribute.

!!! note "Changed in 2.1"
    The ranking moved to Rust and changed: `_score` values and the order differ
    from 2.0, and `_duplicates` / `_topic` were added.
