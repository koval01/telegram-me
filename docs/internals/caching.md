# Caching

## What is cached

| Key (prefix `tme:v2:`) | Value | TTL |
|---|---|---|
| `json:body:page:{mode}:{channel}:{position}` | Rendered `/v1/body` response | page TTL (`CACHE_MODE`: 0 / 10 / 120 s) |
| `page:{mode}:{channel}:{position}` | Parsed page (raw dict as JSON) | page TTL; for the latest page with the feed enabled: `max(page TTL, FEED_STALE_TTL)` |
| `more:{mode}:{channel}:{direction}:{position}` | Rendered `/v1/more` response | page TTL |
| `preview:{channel}` | Parsed preview, or `null` for unknown channels | `PREVIEW_CACHE_TTL` (1 h), 60 s for unknown |
| `feed:popularity` | Sorted set channel → score | — (decayed by the refresher) |
| `feed:refresher-leader` | Token of the leading refresher | 30 s, renewed every 5 s |

`{mode}` is the parser mode, so `full` and `simplified` instances can share a
Redis without mixing results. Channel names are lower-cased.

Every value is stored together with the time it was written
(`"<unix time>\n<payload>"`), which lets callers apply their own freshness
rules on top of the backend expiry — the feed's stale-while-revalidate does
exactly that.

## Backends

**Redis / Valkey** (`REDIS_URL` set): shared by all workers and servers.
Commands time out after `REDIS_TIMEOUT` (0.5 s). The first failure opens a
circuit breaker: Redis is skipped for 30 seconds and requests are served
uncached, so a Redis outage costs at most one slow request per worker, not one
per request. Responses are parsed by hiredis (C).

**Memory** (default): an LRU dictionary per worker, bounded by
`MEMORY_CACHE_MAX_MB` of stored text, with per-entry expiry.

## Pre-rendered responses

For `/v1/body` and `/v1/more` the cached value is the final JSON. When a page
is loaded it is parsed, validated and serialized once; every cache hit is
served as bytes. This makes cache hits about 5× cheaper than re-validating the
parsed page on each request (see [Performance](performance.md)).

## Request coalescing

`SingleFlight` keys in-flight work by cache key. While t.me is being asked for
a page, every other request for the same page waits for that one task instead
of sending its own request — a burst of 1000 requests for a cold channel
produces one upstream request. A client that disconnects does not cancel the
shared task.

## Upstream protection

- A per-process semaphore caps simultaneous requests to t.me
  (`TELEGRAM_MAX_CONCURRENCY`, 32).
- Unknown channels are cached as `null` for 60 s.
- Cloudflare edge caching is enabled through `Cache-Control` (see
  [Reverse proxy](../deployment/reverse-proxy.md#cloudflare)).
