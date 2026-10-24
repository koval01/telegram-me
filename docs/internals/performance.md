# Performance

All numbers below were measured on the same machine (Apple Silicon laptop,
Python 3.14) while preparing 2.0, with t.me replaced by recorded pages so that
only the service itself is measured. Treat them as relative, not absolute.

## 1.x → 2.0, in-process

Median latency of one request, upstream served from fixtures:

| Request | 1.x | 2.0 | |
|---|---|---|---|
| Parse five channel pages (sum) | 33.7 ms | 25.0 ms | −26 % |
| `/v1/body/durov`, uncached | 14.5 ms | 7.1 ms | 2× faster |
| `/v1/body/tginfo`, uncached | 24.5 ms | 15.5 ms | 1.6× faster |
| `/v1/body/durov`, cached | — (no page cache) | 0.9 ms | 16× faster |
| `/v1/body/tginfo`, cached | — | 1.1 ms | 22× faster |
| `/v1/feed`, 5 channels, cached pages | 81.9 ms | 5.4 ms | 15× faster |

## Two-level cache

A repeated request used to cost one Redis round trip when `REDIS_URL` was set:
production's Upstash is ~90 ms away over TLS, so almost all of a cached
response's time was the `GET`. Each worker now answers from its in-process
cache first (Rust, moka) and goes to Redis only on a miss (see
[Caching](caching.md#levels)).

Median / p95 of 200 repeated requests for `@durov` (one worker, ASGI called
directly, upstream served from fixtures, Apple M-series, Python 3.13). The
90 ms Redis is a local Valkey 9.1 behind a proxy that delays each direction
by 45 ms; "cache" is the time spent in `cache.get` per request:

| Request | Redis 90 ms: 2.0.0 | Redis 90 ms: now | Redis local: 2.0.0 | Redis local: now | No Redis: 2.0.0 | No Redis: now |
|---|---|---|---|---|---|---|
| `/v1/body/durov` | 95.0 / 97.4 ms (cache 94.3) | 0.10 / 0.12 ms (cache 0.04) | 0.35 / 0.69 ms | 0.10 / 0.13 ms | 0.15 / 0.16 ms | 0.10 / 0.11 ms |
| `/v1/post/durov/<id>` | 96.7 / 98.5 ms (cache 94.7) | 0.25 / 0.28 ms (cache 0.04) | 0.88 / 1.08 ms | 0.25 / 0.36 ms | 0.29 / 0.33 ms | 0.25 / 0.26 ms |
| `/v1/more/durov/before/<id>` | 95.0 / 96.1 ms (cache 94.4) | 0.09 / 0.10 ms (cache 0.03) | 0.34 / 0.54 ms | 0.09 / 0.12 ms | 0.11 / 0.12 ms | 0.09 / 0.10 ms |
| `/v1/preview/durov` | 94.4 / 96.0 ms (cache 93.9) | 0.08 / 0.08 ms (cache 0.003) | 0.30 / 0.53 ms | 0.08 / 0.11 ms | 0.08 / 0.09 ms | 0.08 / 0.08 ms |
| Mixed workload, 3 000 requests, 8 clients | 51.7 s | 33.3 s | 2.31 s | 2.33 s | 2.27 s | 2.04 s |
| Peak RSS of that run | 101 MiB | 120 MiB | 135 MiB | 166 MiB | 153 MiB | 152 MiB |

- The first read of a key on a worker still costs one Redis round trip
  (`GET` and `PTTL` in one pipeline), as before; so does a write. The mixed
  workload is mostly first reads (every request names a new channel alias),
  which is why it gains less.
- Without Redis the native cache replaces the Python LRU: a hit no longer
  decompresses in Python (`/v1/body` 0.15 → 0.10 ms), and a 95 KB page is
  stored in 83 µs instead of 461 µs (zlib on 3.13; Python 3.14's zstd is
  closer).
- With Redis each worker now also holds what it read or wrote in the last
  `MEMORY_CACHE_MAX_TTL` (10) seconds, bounded by `MEMORY_CACHE_MAX_MB`:
  +19–31 MiB of peak RSS in this run, where everything was read within 10 s.
- Releasing the GIL around reads and writes was measured and rejected: next
  to two CPU-bound Python threads (pydantic on the parsing threads) a 95 KB
  read took 6.3 ms (p50) with the GIL released, 38 µs with it held.

## Native feed assembly

A feed used to decode every cached page into Python objects, build a ranker
input per post and serialise the result with pydantic, on the event loop with
the GIL held: ~41 ms of a 100-channel feed. It is now one call to
`telegram_me_native.build_feed` with the GIL released (cached page bytes in,
response bytes out). `scripts/audit.py`, in process, same machine, 100-channel
feeds of 2 000 posts:

| | Before | After |
|---|---|---|
| `POST /v1/feed`, 100 channels | 83.7 ms | 42.7 ms |
| CPU of that feed on one core (`RAYON_NUM_THREADS=1`) | 131 ms | 106 ms |
| Python allocations of that feed | 20.8 MiB | 9.8 MiB |
| Cached `/v1/body` next to a feed loop, p50 / p99 | 15.2 / 61.6 ms | 2.3 / 10.5 ms |
| Peak RSS of a worker serving 200 such feeds (Linux) | 220 MiB | 218 MiB (noise) |

Details, per-endpoint costs and what moves next: [Rust roadmap](rust-roadmap.md).

## Request coalescing

`scripts/coalescing_load.py` starts 6 worker processes (think two servers of
three granian workers) on one Valkey, each with 32 clients calling its app
directly. Every client picks requests at random from the same 37: each
recorded channel through `/v1/body` (latest and around a post), `/v1/post`,
`/v1/more` both ways, three previews, a 6-channel feed and a GraphQL query
with a body and the feed. t.me is replaced by recorded pages answered after
150 ms; `CACHE_MODE=normal` (10 s), so the 27 distinct upstream requests
expire twice in a 25 s run. Two runs each, Apple M-series with 8 cores:

| | main (with the native feed) | this change, `SHARED_COALESCING=false` | this change |
|---|---|---|---|
| Upstream requests, local Valkey | 417 / 412 | 421 / 428 | **75 / 75** |
| Upstream requests, Valkey 90 ms away | 434 / 422 | 414 / 413 | **75 / 75** |
| Per distinct upstream request | 15.3–16.1 | 15.3–15.9 | **2.8** (once per TTL period) |
| Requests served in 25 s, local | 117 k / 117 k | 530 k / 537 k | 548 k / 533 k |
| CPU per request, local | 1.41 / 1.43 ms | 0.30 / 0.29 ms | 0.29 / 0.29 ms |
| CPU per request, 90 ms | 1.43 / 1.41 ms | 0.27 / 0.27 ms | 0.27 / 0.27 ms |
| p99 latency, local | 917 / 902 ms | 206 / 203 ms | 199 / 206 ms |
| Longest event-loop stall of a worker, local | 845 / 905 ms | 246 / 200 ms | 224 / 228 ms |

- The lease is what removes the duplicates across processes: 75 upstream
  requests is the minimum for this run (27 distinct, renewed at most twice),
  and it holds with Redis 90 ms away. Without it every process fetches each
  page itself, as before.
- Sharing the rendering is what cuts the CPU: identical feeds are built once
  (and, for GraphQL, decoded once) instead of once per request, `/v1/post`
  is cached rendered, and concurrent `/v1/body` misses render once. The freed
  CPU went into serving 4–5× as many requests in the same time, with shorter
  event-loop stalls.
- A Redis command that times out opens the worker's circuit breaker; that
  worker then fetches on its own (one fetch per key, as before the lease)
  until it closes. Seen in development when a worker's event loop stalled
  for over 2 s (before GraphQL feeds were decoded once), not in the runs
  above.

The cached hot path is unchanged (one worker, median of 2 500 hits, ASGI
called directly):

| Hit | main | this change |
|---|---|---|
| `/v1/body/durov` | 0.101 ms | 0.101 ms |
| `/v1/more/durov/before/<id>` | 0.091 ms | 0.091 ms |
| `/v1/post/durov/<id>` | 0.242 ms | **0.067 ms** (cached rendered) |

Admission is a few native calls on a miss only; a hit adds one native call
that compares the entry's age with its renewal point.

## Page parsing: Python + lexbor → Rust

Median time to parse one recorded channel page (`scripts/benchmark.py`):

| Page | Python + selectolax/lexbor | Rust (html5ever) |
|---|---|---|
| @durov (20 posts, many custom emoji) | 4.9 ms | 3.6 ms |
| @tginfo (20 long posts, many links) | 12.4 ms | 6.3 ms |

The bigger difference is under load: the Rust parser releases the GIL, so
concurrent requests parse pages in parallel instead of queueing on one core
(24 pages on 24 threads: 111 ms → 67 ms on 8 cores, before the optimisations
above).

## Event loop under a refresher tick

Event-loop lag of one worker with Redis (Valkey) and the embedded refresher,
in-process workload of 8 clients × 3 000 requests twice, with a refresher tick
in between that refreshes the ~200 channels the first pass made popular:

| | Worst lag | Median lag | Run time | Breaker trips |
|---|---|---|---|---|
| Parsing on the event loop, unbounded tick | 0.87–1.31 s | 9 ms | 32–36 s | 1 per run |
| 2 parsing threads, ≤ 16 refreshes at a time | 25–122 ms | 1.1 ms | 13 s | none |

Each finished fetch used to parse its page (~3 ms native) and validate and
render it (~0.5–2 ms pydantic) right on the event loop. When a tick's fetches
finished together they ran back to back for about a second, longer than
`REDIS_TIMEOUT`, so a Redis command in flight timed out and opened the
circuit breaker for 30 s. Pages are now parsed on two dedicated threads: the
default executor (up to `cpu + 4` threads) was no faster and, competing with
the event loop for the GIL, tripled its p99 lag (~21 ms instead of ~7 ms).
The refresher refreshes at most 16 channels at a time, which also leaves
upstream slots for user requests.

## Outgoing HTTP client

2 000 HTTPS requests for a 148 KB page, 32 concurrent, against a local TLS
server:

| Client | Requests/s | CPU per request | Peak memory |
|---|---|---|---|
| httpx, HTTP/2 (1.x) | 584 | 1.31 ms | 219 MB |
| httpx, HTTP/1.1 | 378 | 1.85 ms | 151 MB |
| aiohttp | 5 842 | 0.17 ms | 71 MB |
| curl_cffi | 4 444 | 0.23 ms | 58 MB |
| httpr (Rust) | 9 308 | 0.23 ms | 53 MB |
| **pyreqwest (Rust)** — 2.0 | **10 563** | **0.15 ms** | **51–64 MB** |

1.x additionally created a new client — and a new TLS handshake — for every
request to t.me.

## HTTP server

One worker, 64 connections, 10 s (`wrk`), same application:

| Endpoint | uvicorn + httptools + uvloop | granian — 2.0 |
|---|---|---|
| `/healthz` | 15 774 req/s, p99 7.97 ms | **21 120 req/s, p99 3.23 ms** |
| `/v1/body/durov` (cached, 91 KB) | 4 088 req/s, p99 23.9 ms | **4 365 req/s**, p99 31.4 ms |

Pre-rendering cached responses raised the cached `/v1/body` throughput from
~850 to ~4 350 requests per second on one worker.

## Shutdown and memory

| | 1.x | 2.0 |
|---|---|---|
| `docker stop` | 10 s, then `SIGKILL` (uvicorn was not PID 1) | 0.3 s, exit code 0 |
| Redis unreachable, `/v1/preview` | 10 s per request | one 0.5 s timeout, then uncached |
| Container memory, 1 worker, idle | — | ~110 MB (see [Memory](#memory)) |

## Memory

Measured on arm64 with the Docker image (`docker stats`, one worker unless
noted), before and after the memory work that went into 2.0. "Light" is a few
hundred live requests over six channels; "heavy" is 2 × 20 000 requests from 8
concurrent clients over 1 200 distinct channels, with t.me replaced by the
recorded pages.

| Scenario | Before | After |
|---|---|---|
| Idle | 146–153 MiB | 110 MiB |
| Light traffic | 185 MiB | 143 MiB |
| `WEB_CONCURRENCY=2`, idle / light traffic | 217 / 278 MiB | 180 / 224 MiB |
| Heavy traffic, memory cache (64 MB limit) | 458 MiB | 229–253 MiB |
| Heavy traffic, Redis, during / 15 s after | 296–302 / 299 MiB | 190–263 / 155 MiB |
| granian main process (private memory) | 65 MiB | 29 MiB |

What changed and why:

- **The memory cache counted characters, not bytes.** Values were Python
  `str`; one emoji makes CPython store a whole string with four bytes per
  character, so a rendered page of 89 KiB of UTF-8 took 353 KiB, and the
  64 MB limit let a worker hold ~260 MiB of cache. Values are now UTF-8 bytes,
  compressed with zstd level 1 (zlib on Python 3.13), and the limit counts the
  compressed value, the key and the bookkeeping (~230 bytes per entry, measured):

    | Page | As `str` | UTF-8 | In the cache now |
    |---|---|---|---|
    | @durov, rendered | 353 KiB | 89 KiB | 21 KiB |
    | @tginfo, rendered | 700 KiB | 233 KiB | 43 KiB |
    | @stranaua, rendered | 93 KiB | 59 KiB | 13 KiB |

    Decompressing a page (25–120 µs) is faster than encoding the same `str`
    to UTF-8 on every hit was (27–306 µs), and Redis replies are read as
    bytes as well.

- **glibc kept freed memory.** Responses are built from large short-lived
  objects, and after a burst glibc's heap stayed at its peak: with Redis, a
  worker held 97 MiB of heap of which 15 MiB were in use. Workers now call
  `malloc_trim(0)` every [`MEMORY_TRIM_INTERVAL`](../configuration.md#memory_trim_interval)
  seconds (about 1 ms per call), which returned ~80 MiB in that case.
- **The granian main process imported the application** (FastAPI, GraphQL,
  Redis, the HTTP client, the native extension) only to supervise workers. It
  now imports it in the worker factory.
- **FastAPI ran the dependencies in a thread pool** (plain `def`), which kept
  an extra thread — with its own malloc arena — in every worker. They are
  `async def` now.

Evaluated and not adopted (heavy traffic, measured while the parser was still
Python; compare the rows with each other):

| Option | Heavy traffic, memory cache | Verdict |
|---|---|---|
| glibc (default) | 205 MiB | kept |
| `MALLOC_ARENA_MAX=2` | 202 MiB | within noise |
| glibc with fixed `MALLOC_MMAP_THRESHOLD_` / `MALLOC_TRIM_THRESHOLD_` | 211 MiB | within noise; with Redis it recovered only half of what `malloc_trim` does |
| jemalloc (`LD_PRELOAD`), default / 1 s decay / 2 arenas | 212 / 206 / 200 MiB, +5–17 MiB idle | no gain |
| `PYTHONMALLOC=malloc` + jemalloc | 200–227 MiB, +15 MiB idle | no gain |
| mimalloc | 256 MiB | worse |
| tcmalloc | 252 MiB | worse |
| Fork workers from a preloaded main process + `gc.freeze()` (2 workers) | 273 instead of 333 MiB | not adopted: granian forks from a multi-threaded supervisor ("use of fork() may lead to deadlocks"), so workers keep being spawned |

Parsing does not keep memory: pages are parsed natively and returned as one
JSON string, and the memory regression tests show no growth over hundreds of
uncached parses. `WEB_CONCURRENCY`, `MEMORY_CACHE_MAX_MB` and
`WORKERS_MAX_RSS_MB` keep their defaults: with real accounting, 64 MB of cache
now holds 4–16 times more pages than before within a fixed budget.

### Guarding against regressions

`tests/test_memory.py` runs thousands of requests through the app and fails
when memory keeps growing after a warm-up or the cache exceeds its limit; it
catches both a leaked parsed page per request and leaked native memory.
`rust/telegram-me-native/tests/cache_memory.rs` counts every allocation of
the in-process cache and fails when its accounting no longer covers them. To
see the numbers:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 200
```

## Reproducing

```bash
uv run python scripts/benchmark.py   # in-process, recorded pages, no network
uv run python scripts/audit.py --stages   # per endpoint (wall, CPU, allocations) and per stage
docker run -d --name tme-valkey -p 6390:6379 valkey/valkey:8
uv run python scripts/coalescing_load.py --redis redis://127.0.0.1:6390/0
```

For server throughput, start an instance and point a load generator at it:

```bash
make up                              # API + Valkey on :8000
wrk -t2 -c64 -d10s --latency http://127.0.0.1:8000/healthz
```
