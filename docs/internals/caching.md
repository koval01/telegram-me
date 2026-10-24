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
(`"<unix time>\n<payload>"` in Redis), which lets callers apply their own
freshness rules on top of the backend expiry — the feed's
stale-while-revalidate does exactly that.

## Levels

Every worker has an **in-process cache**. With `REDIS_URL` set it is the first
level (L1) in front of **Redis / Valkey** (L2), which every worker and server
shares; without Redis it is the only level. Services see one `Cache` either
way (`TwoLevelCache` or `MemoryCache` in `cache.py`).

| Request repeated on the same worker | 2.0.0 | Now (L1 hit) |
|---|---|---|
| `/v1/body`, Redis 90 ms away (Upstash) | 95 ms | 0.10 ms |
| `/v1/body`, Redis on the same host | 0.35 ms | 0.10 ms |
| `/v1/body`, no Redis | 0.15 ms | 0.10 ms |

See [Performance](performance.md#two-level-cache) for every endpoint.

### In-process level

`telegram_me_native.LocalCache`: Rust, on `moka`'s concurrent cache.

- **LRU, bounded by real memory.** An entry weighs its key, its stored value
  and 512 bytes of bookkeeping (moka's entry, hash-table, LRU and timer nodes:
  ~390 bytes requested, measured with a counting allocator, plus malloc's
  rounding). The total stays within `MEMORY_CACHE_MAX_MB` right after every
  write; `rust/telegram-me-native/tests/cache_memory.rs` checks that what is
  accounted covers what is really allocated. Every new entry is admitted (no
  TinyLFU admission filter): a page just fetched is about to be read again.
- **Compressed values.** zstd level 1 (libzstd, C): a rendered page takes
  12–43 KiB instead of 44–233 KiB of UTF-8, and reading one back
  (decompressing straight into a new `bytes`) takes ~40 µs for a 95 KB page.
  Values that do not shrink are stored as they are.
- **Per-entry expiry** through moka's `Expiry`: each entry lives for its own
  TTL. Expired entries are never returned and are removed by moka's timer
  wheel within about a second, read or not.
- **The GIL is kept.** Reads and writes take tens of microseconds and run on
  the event loop. Released, the GIL goes to a parsing thread busy in pydantic,
  and the event loop waits for it to come back: with two CPU-bound Python
  threads running, a read took 6.3 ms (p50) instead of 38 µs.

### Redis level

Commands time out after `REDIS_TIMEOUT` (0.5 s). The first failure opens a
circuit breaker: Redis is skipped for 30 seconds, so a Redis outage costs at
most one slow request per worker, not one per request. Responses are parsed
by hiredis (C).

A feed or previews request runs up to 100 cache lookups at once, so the
connection pool (`2 × TELEGRAM_MAX_CONCURRENCY` per worker, at least 32) makes
a burst wait up to `REDIS_TIMEOUT` for a free connection. A command that gets
none is skipped as a miss; an exhausted pool is a local limit, so it does not
open the circuit breaker.

### How the levels work together

- **Read:** L1 first. On a miss, one round trip to Redis (a pipeline of `GET`
  and `PTTL`) returns the entry and its remaining TTL; L1 keeps a copy for that
  remaining TTL, at most `MEMORY_CACHE_MAX_TTL` (10 s). Concurrent misses of
  one key share a single Redis read. A copy never replaces a newer entry that
  was written to L1 while the read was in flight.
- **Write:** to both levels, with the same store time. L1 keeps the entry for
  `min(TTL, MEMORY_CACHE_MAX_TTL)`.
- **Store time:** a copy keeps the time the entry was first stored, so an
  entry has the same age on every server and the feed's
  stale-while-revalidate works unchanged.
- **Redis down or circuit breaker open:** L1 works alone. Reads that miss L1
  are misses; writes go to L1 only, for their full TTL, since there is no
  shared copy to pick up instead.

**Why `MEMORY_CACHE_MAX_TTL`.** Two servers (and the feed worker) share Redis
but not their L1s, and nothing tells a server that another one has written a
newer entry. For most keys the staleness is bounded by their TTL anyway, which
is acceptable: a rendered page lives for the page TTL on every level. The
latest page of a channel, however, is kept for the feed's stale window
(`FEED_STALE_TTL`, 15 min) while it counts as fresh only for the page TTL
(10 s) or `FEED_FRESH_TTL` (60 s). Without a cap, a server would keep its old
copy for up to 15 minutes and re-fetch the page from t.me when it got old,
instead of reading the copy the refresher (or the other server) had just put
in Redis; with the feed worker, the API servers would ignore its work. With
the cap, a server sees a newer entry in Redis at most 10 seconds after it was
written. The cost is one Redis read per key and worker every 10 seconds at
most, for keys that are being read.

Values are UTF-8 bytes on both levels. Keeping JSON as `str` cost up to four
bytes per character (a single emoji widens the whole string) and an encode on
every cache hit.

## Pre-rendered responses

For `/v1/body` and `/v1/more` the cached value is the final JSON. When a page
is loaded it is parsed, validated and serialized once; every cache hit is
served as bytes. This makes cache hits about 5× cheaper than re-validating the
parsed page on each request (see [Performance](performance.md)).

## Request coalescing

Identical concurrent requests do the work once: one request to t.me and one
render, wherever they arrive. The pieces below are measured in
[Performance](performance.md#request-coalescing); the scheduling core (flight
table, upstream gate, renewal points) is Rust in
[`sched.rs`](https://github.com/koval01/telegram-me/blob/main/rust/telegram-me-native/src/sched.rs),
the Python services only wire it up.

### In one worker

`SingleFlight` keys in-flight work by cache key, separately for parsed pages,
rendered bodies, rendered posts, `/v1/more` fragments and previews. While t.me
is being asked for a page, every other request for it waits for that one task
instead of sending its own — a burst of 1000 requests for a cold channel
produces one upstream request. A client that disconnects does not cancel the
shared task. Because REST and GraphQL resolve through the same services, a
`GET /v1/body/durov` and a GraphQL `body(channel: "durov")` share the fetch
and the render too; `/v1/post/durov/531` and `/v1/body/durov?position=531`
share the page around post 531; a feed and a single-channel request share the
channel's latest page; identical feed requests are built, ranked and rendered
once.

Keys are normalized before they are compared: channel names are
case-insensitive (`Durov` and `durov` are one key, for feeds as well) and an
absent position is position 0. Pages around *different* positions are not
treated as interchangeable even when they contain the same post: every page
load carries its own per-post `view` tokens (and t.me renders footers and
forwards slightly differently around the anchor), so serving one for the
other would change responses.

### Across workers and servers

Every granian worker of every server has its own single flights, so on their
own a hot key that expires is fetched once per worker per server — 6 times
with two servers of three workers. With Redis, the worker that leads locally
first takes a lease, `tme:v2:lease:<key>`, in one round trip that also reads
the cached value (a Lua script: `SET NX PX` plus `GET`):

- the cached value is already fresh (another process just wrote it): use it
  and keep a copy in the in-process cache, no fetch;
- the lease is taken: fetch, write the cache, release the lease;
- another process holds the lease: poll with jittered back-off (25 ms growing
  to 250 ms), retrying the lease on every round, until the holder's result is
  in the cache. A holder that failed with a domain error (not found, parse,
  upstream) leaves that error in the lease for 2 s, so waiters — and requests
  arriving meanwhile — fail the same way instead of all retrying t.me.

The lease lives 3 s and its holder extends it every second while the fetch
runs, so a holder that crashed or lost Redis is taken over within 3 s, however
long t.me takes for a live one. No waiter waits longer than
`2 × TELEGRAM_TIMEOUT + 3` seconds in all, and none waits for Redis longer
than `REDIS_TIMEOUT` per command: when Redis is slow, down or its circuit
breaker is open, a worker simply fetches on its own. The local single flight
still applies then, so an outage costs one fetch per worker (the behaviour
before the lease), never a thundering herd. `SHARED_COALESCING=false` turns
the lease off. Without Redis there is nothing to coordinate through, and each
worker coalesces on its own.

The feed refresher goes through the same path: a refresh that a user request
on another server is already running joins it, and a page that some process
fetched within the last half refresh interval is not fetched again.

### Early renewal

Each entry gets a renewal point in the last `EARLY_REFRESH_WINDOW` (10 %) of
its freshness, derived from the time it was stored, so that every reader in
every process agrees on it and entries stored together (a refresher tick)
renew spread out. The first request that reads a page, rendered body,
rendered post or `/v1/more` entry past that point is still answered from the
cache and starts a background refresh; later reads of the same entry in that
process do not ask again, and the refresh is coalesced like any fetch. A hot
key is therefore fetched about once per TTL and never expires under load —
no miss storm, and nobody waits for t.me. A probabilistic scheme such as
XFetch was tried and rejected: with hundreds of reads per second it renews
about `ln(reads per window)` windows before expiry, doubling the upstream
traffic of exactly the hottest keys. Long TTLs (previews, the feed's stale
window) are also shortened by a random part of the window, so entries
written together do not expire together.

## Upstream protection

- **Fair concurrency cap.** Each worker sends at most
  `TELEGRAM_MAX_CONCURRENCY` (32) requests to t.me at once. Free slots go
  round-robin over *flows* — a channel, or one feed or previews request as a
  whole — rather than first come, first served, so a 100-channel feed does
  not make a single-channel request wait behind it. One flow holds at most
  three quarters of the slots. Background refreshes hold at most half and
  only get a slot while no user request waits; a user who joins a refresh
  still waiting for a slot raises it to user priority.
- **Bounded queues, load shedding.** At most `UPSTREAM_QUEUE_LIMIT` requests
  (default 16 per slot) wait for a slot, and at most `MAX_WAITING_REQUESTS`
  (4096) requests wait for upstream work of any kind per worker. Beyond that a
  request is answered `503` with `Retry-After` (estimated from the queue
  length and how long a slot is held) instead of queueing without limit; a
  feed or previews request skips the channels that were shed. Background
  work beyond the bounds is dropped silently.
- Unknown channels are cached as `null` for 60 s.
- Cloudflare edge caching is enabled through `Cache-Control` (see
  [Reverse proxy](../deployment/reverse-proxy.md#cloudflare)).
