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
catches both a leaked parsed page per request and leaked native memory. To
see the numbers:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 200
```

## Reproducing

```bash
uv run python scripts/benchmark.py   # in-process, recorded pages, no network
```

For server throughput, start an instance and point a load generator at it:

```bash
make up                              # API + Valkey on :8000
wrk -t2 -c64 -d10s --latency http://127.0.0.1:8000/healthz
```
