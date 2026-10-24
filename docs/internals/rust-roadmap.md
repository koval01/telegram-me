# Rust roadmap

Where a worker still spends its time in Python, which way the remaining hot
path moves to Rust, and in which order. The rule this follows: heavy work runs
natively; native code we write ourselves is Rust (ready-made components may be
C, C++ or Rust); Python is glue: business rules, wiring, orchestration and
scripts, nothing on the hot path.

Two things do not change on the way: the REST `/v1` responses and the
GraphQL schema (both pinned by `tests/snapshots/`, REST byte for byte), and
the deployment (Docker, compose, `deploy/remote-deploy.sh`, 1 GB VMs).

## Audit

Measured with [`scripts/audit.py`](https://github.com/koval01/telegram-me/blob/main/scripts/audit.py)
on Apple Silicon (8 cores, Python 3.13), in process, with t.me replaced by the
recorded pages and the in-process cache (the L1 of the
[two-level cache](caching.md#levels)) as the cache; memory on Linux (arm64,
Python 3.14). The baseline is `main` after the two-level cache (PR #201).
"Allocated" is the peak of Python allocations while serving one request
(tracemalloc). Treat absolute numbers as relative.

```bash
uv run python scripts/audit.py --stages        # per endpoint and per stage
RAYON_NUM_THREADS=1 uv run python scripts/audit.py  # CPU cost on one core
uv run python scripts/audit.py --memory 200    # RSS of a feed loop (Linux)
```

### Cost per request

| Request | Wall | CPU (all threads) | Allocated | Response |
|---|---|---|---|---|
| `GET /healthz` | 0.03 ms | 0.03 ms | 15 KiB | 15 B |
| `GET /v1/body/durov`, cached | 0.11 ms | 0.11 ms | 105 KiB | 91 KB |
| `GET /v1/body/tginfo`, cached | 0.17 ms | 0.17 ms | 248 KiB | 238 KB |
| `GET /v1/post/durov/531`, cached | 0.33 ms | 0.33 ms | 278 KiB | 3 KB |
| `GET /v1/preview/durov`, cached | 0.07 ms | 0.07 ms | 18 KiB | 0.5 KB |
| `POST /v1/previews`, 100 channels, cached | 1.14 ms | 1.10 ms | 327 KiB | 65 KB |
| `POST /graphql`, one `body`, cached | 0.95 ms | 0.95 ms | 122 KiB | 175 B |
| `POST /v1/feed`, 6 channels, cached | 8.97 ms | 15.8 ms | 2.0 MiB | 0.6 MB |
| `POST /v1/feed`, 30 channels, cached | 27.0 ms | 54.0 ms | 6.2 MiB | 0.9 MB |
| `POST /v1/feed`, 100 channels, cached | 83.7 ms | 170 ms | 20.8 MiB | 1.7 MB |
| `GET /v1/body/durov`, uncached | 4.90 ms | 4.86 ms | 742 KiB | 91 KB |
| `GET /v1/body/tginfo`, uncached | 9.40 ms | 9.35 ms | 1.5 MiB | 238 KB |

The 100-channel feeds use aliases of the six recorded channels, each with its
own username: 2 000 posts with many duplicate stories, a heavy case for the
duplicate search.

**Cold page pipeline**, per recorded channel page (ms, single thread):

| Page | parse (Rust) | `from_json` | validate | dump | encode | zstd | `decode` |
|---|---|---|---|---|---|---|---|
| @durov | 3.55 | 0.13 | 0.30 | 0.28 | 0.10 | 0.42 | 0.13 |
| @telegram | 2.84 | 0.09 | 0.24 | 0.22 | 0.07 | 0.27 | 0.09 |
| @tginfo | 5.97 | 1.00 | 0.66 | 0.56 | 0.21 | 1.14 | 1.00 |
| @nebudetgg | 2.06 | 0.16 | 0.21 | 0.18 | 0.06 | 0.23 | 0.16 |

Parsing releases the GIL; everything after it (`from_json` to Python
objects, pydantic validation and serialization, the cache encoding) holds it:
0.5–2.6 ms per page. A `/v1/body` miss also validates and renders the page a
second time on the event loop (`body_json` → `body()`), although the parsing
thread already rendered it.

**Feed build, 100 cached pages, 2 000 posts** (2.0 code, ms):

| Stage | Where | Time |
|---|---|---|
| decode every page to Python objects | event loop, GIL held | 33 |
| build a `Candidate` per post | event loop, GIL held | 5.5 |
| rank | worker thread, Rust, GIL released | 35 (8 cores) / 82 (1 core) |
| build entries, validate, dump | event loop, GIL held | 2.3 |

**Event-loop stall.** Cached `/v1/body/durov` from two clients at 50 req/s
each, latency counted from when each request was due, next to one client
building 100-channel feeds in a loop:

| | p50 | p99 | Feeds/s |
|---|---|---|---|
| 8 cores | 15.2 ms | 61.6 ms | 11.2 |
| 1 core (`RAYON_NUM_THREADS=1`) | 2.2 ms | 61.0 ms | 7.4 |

(Alone, that request takes 0.11 ms.)

### By component

| Component | Python work per request | Bound by | Notes |
|---|---|---|---|
| granian + FastAPI routing, validation, 3 middlewares | ~30 µs in process; ~50 µs per request on a real socket (21 k req/s per worker on `/healthz`) | CPU (Python) | the per-request floor of every endpoint |
| Response building | cached `/v1/body`, `/v1/more`: none (bytes from the cache); `/v1/post`: decode the page, validate one post, dump (0.25 ms); `/v1/preview(s)`: decode, validate, dump (~10 µs per channel) | CPU (Python, pydantic-core) | |
| Page pipeline on a miss | 0.5–2.6 ms per page after the native parse, plus the second render of `/v1/body` | CPU (Python, pydantic-core) | runs on 2 parsing threads, but holds the GIL |
| Cache | L1: native (moka + zstd), tens of µs with the GIL held; L2: the asyncio redis-py client (hiredis parser) per command plus the round trip | L1 CPU, L2 I/O | the two-level cache (PR #201) |
| Single-flight (`coalesce.py`) | one task, a shield and a dict entry per call: ~40 µs, mostly an extra event-loop turn | CPU (Python) | cross-worker coalescing is in progress elsewhere |
| Rate limit, proof of work (`protection.py`) | checked in Rust (`ratelimit.rs`, `pow.rs`) in under a microsecond, inside one pure ASGI middleware; the Python sliding window it replaced cost +4 µs per request | CPU (Rust) | off by default; PR #202 |
| Feed assembly | see above: 41 ms of GIL-held Python per 100 channels | CPU (Python) | **stage 1** |
| Popularity, leader lock | one Redis pipeline per feed (`ZINCRBY` × channels), one `EVAL` per tick | I/O | redis-py builds the pipeline in Python |
| Refresher loop | scheduling only; the pages go through the page pipeline | I/O + page pipeline | ≤ 16 refreshes at a time |
| HTTP client (`client.py`) | a semaphore and an await; `/v1/more` also `json.loads` of the reply (~0.2 ms) | I/O (Rust, pyreqwest) | |
| GraphQL (strawberry) | ~0.85 ms per query on top of the services | CPU (Python) | query parsing, validation, resolver per field |
| Worker model | one process per worker, each with its own interpreter, L1 cache and HTTP pool | memory | see below |

### Memory per worker

Linux, arm64, Python 3.14 (`scripts/memory_profile.py`, `scripts/audit.py --memory`):

| | RSS | Anonymous |
|---|---|---|
| Interpreter alone | 9.0 MiB | 3.4 MiB |
| granian main process (supervisor) | 43.7 MiB | 26.2 MiB |
| Worker with the application built, idle | 85.3 MiB | 63.2 MiB |
| Worker after 20 000 mixed requests over 1 200 channels (in-process cache 41 MiB accounted) | 187–212 MiB, peak 220 MiB | |
| Worker serving 200 100-channel feeds | peak 220 MiB | |

Python's own live objects stay at ~13 MiB through the mixed workload
(tracemalloc); the rest is the interpreter and its imports, the in-process
cache, native buffers and what malloc keeps between bursts.

A worker's baseline is the interpreter and the imported application (FastAPI,
pydantic, strawberry, redis-py, the native extension), plus up to
`MEMORY_CACHE_MAX_MB` of L1. `WEB_CONCURRENCY` workers duplicate all of it,
the L1 included.

### What it shows

- Nearly all CPU-bound work outside the parser and the ranker was in
  **feed assembly**: 41 ms of Python per 100-channel feed with the GIL held,
  ~20 MiB of short-lived Python objects, and a stall of every other request
  on the worker for that long. That is the biggest single item.
- Next is the **page pipeline after the parser** (0.5–2.6 ms of GIL-held
  pydantic per page, twice for a `/v1/body` miss). It matters for refresher
  ticks and cold traffic, not for cache hits.
- **Cache hits are already cheap** (0.07–0.33 ms). What remains there is the
  per-request floor of granian's ASGI bridge and FastAPI, and pydantic for
  `/v1/post` and previews.
- **GraphQL** costs ~0.85 ms of Python per query regardless of the data.
- The I/O-bound parts (HTTP client, Redis) are already native or C; what
  Python adds there is per-command overhead, not throughput.
- The **ranker** itself (Rust) is now the largest CPU item of a feed:
  ~80 ms per 2 000 posts on one core. Optimising it is independent of where
  the glue runs.

## Options

### (a) Keep the granian/FastAPI shell, move whole pipelines native

Each endpoint's work becomes one native call: fetch, parse, validate and
render bytes, with the GIL released; native cache, single-flight and rate
limiter. Python keeps routing, settings, feature flags, the error mapping,
the feed's freshness rules and the refresher's scheduling.

- **For:** incremental, every step ships alone and is checked against the
  byte snapshots; OpenAPI (from the pydantic models), GraphQL and the CLI stay
  as they are; business rules stay in Python; Docker and the deploy scripts do
  not change. It is the path the parser, the ranker and the L1 cache already
  took.
- **Against:** the per-request floor stays (granian's ASGI bridge, FastAPI,
  ~50 µs per request, ~20 k req/s per worker), and so does a full interpreter
  per worker. GraphQL stays in Python.

### (b) A Rust HTTP server (axum/hyper on tokio) serving `/v1`

The server is Rust; Python is embedded (PyO3) or called only for business
rules.

- **For:** the lowest floor (tens of µs and several times the throughput per
  core), one multi-threaded process instead of one process per worker, so one
  L1 cache and one HTTP pool per server instead of one per worker: the biggest
  memory saving.
- **Against:** a big-bang switch of the server, router, middleware, error
  mapping and OpenAPI generation; an embedded interpreter brings the GIL back
  for every rule it runs, so rules must become data or Rust to get the
  benefit; GraphQL would need a Rust port (async-graphql) with an identical
  schema and errors, or stay a second, Python service. The rules stop being
  "easy to change in Python".

### (c) A Rust core service and a thin Python control plane

A Rust binary serves `/v1` (and later GraphQL) and runs the refresher; Python
keeps configuration, the CLI, fixtures and scripts, and possibly GraphQL as a
separate process calling the core.

- **For:** the same runtime gains as (b) without embedding; a clean boundary.
- **Against:** two processes per VM to deploy, configure and monitor; the
  control plane needs an interface (config file, admin API); the same
  big-bang server switch as (b), and everything that today is Python glue
  (feature flags, freshness rules) has to be re-expressed in Rust or in
  config.

### Recommendation: (a), staged

Option (a) gets the CPU and GIL wins where they are measured (feed assembly,
the page pipeline, GraphQL resolution) without a risky switch. Each stage moves
a whole pipeline into `telegram-me-native`, ships on its own, is verified by
the byte snapshots and has its own measured win. When the pipelines are
native, the process is already a Rust core behind a Python shell: what is left
in Python is the ~50 µs ASGI/FastAPI floor and one interpreter per worker.
Whether that is worth replacing with an axum front (moving towards (c)) is
decided then, on numbers (stage 6), not now.

## Stages

Each stage: snapshots (`make snapshots`, raw digests) unchanged, its own
before/after numbers, its own PR.

1. **Native feed assembly** — *done in this release.* `FeedService` hands the
   cached page bytes to `telegram_me_native.build_feed` and gets the response
   bytes back (one call, GIL released): reading pages, ranking signals,
   ranking, rendering. Python keeps the stale-while-revalidate rules and
   popularity. See the [results](#stage-1-results).
2. **Native page pipeline.** Parse, validate and render in one call:
   `parse_channel_page` returns the cache encoding and the rendered
   `/v1/body` (and `/v1/more`) bytes directly, with the schema checks pydantic
   does today (URL normalisation — pydantic-core uses the same `url` crate —,
   literal values, defaults, the field validators, dropping posts that do not
   validate). The pydantic models stay as the documented schema (OpenAPI,
   GraphQL types) and become the oracle of a differential test over the
   fixtures and generated posts. Removes 0.5–2.6 ms of GIL-held Python per
   page and the second render of a `/v1/body` miss; makes refresher ticks
   GIL-free.
3. **Native cached reads.** `/v1/post`, `/v1/preview` and `/v1/previews`
   rendered natively from cached bytes (extract one post, render previews), so
   no cache hit builds Python objects. Builds on stage 2's renderer.
4. **Native Redis level and popularity.** A Rust Redis client (redis-rs or
   fred, on the tokio runtime) behind the existing `Cache` protocol and the
   two-level cache, plus popularity and the leader lock; one round trip per
   feed for all its pages (`MGET`). Coordinated with the cross-worker request
   coalescing work, which also lives on Redis.
5. **GraphQL resolution.** Resolvers read native-rendered bytes and the
   heaviest selections (`body.posts`) are projected natively; strawberry keeps
   the schema, so the SDL snapshot does not change. Removes most of the
   ~0.85 ms per query.
6. **The shell, decided on numbers.** With 1–5 done, measure what the ASGI
   bridge, FastAPI and the middlewares cost and what one interpreter per
   worker costs in RAM. If it is worth it, serve `/v1` from an axum front in
   the same image (Python keeping GraphQL, docs and the CLI) or run one
   worker with native threads instead of several processes. The rate limiter
   and proof of work are already native (`ratelimit.rs`, `pow.rs`, PR #202).

Not planned: the ranker's algorithm. Its speed is separate work: at ~80 ms per
2 000 posts on one core it is now the largest CPU item of a feed.

## Stage 1 results

Same machine, same script, `main` (with the two-level cache) against this
release. Responses are byte-identical (`tests/snapshots/raw_digests.json`).

| | Before | After | |
|---|---|---|---|
| `POST /v1/feed`, 6 channels, wall | 8.97 ms | 6.17 ms | −31 % |
| `POST /v1/feed`, 30 channels, wall | 27.0 ms | 14.7 ms | −45 % |
| `POST /v1/feed`, 100 channels, wall | 83.7 ms | 42.7 ms | −49 % |
| `POST /v1/feed`, 100 channels, CPU on one core | 131 ms | 106 ms | −19 % |
| `POST /v1/feed`, 6 / 30 channels, CPU on one core | 11.3 / 40.6 ms | 9.1 / 32.5 ms | −20 % |
| `POST /v1/feed`, 100 channels, allocated by Python | 20.8 MiB | 9.8 MiB | −53 % |
| Python allocations, 6-channel feed | 2.0 MiB | 1.1 MiB | −44 % |
| Cached `/v1/body` next to a feed loop, p50 / p99 (8 cores) | 15.2 / 61.6 ms | 2.3 / 10.5 ms | 6.6× / 5.9× |
| Same, one core, p99 | 61.0 ms | 4.7 ms | 13× |
| 100-channel feeds per second, one worker (8 cores / 1 core) | 11.2 / 7.4 | 23.3 / 9.3 | 2.1× / 1.3× |

Resident memory does not change measurably: on Linux a worker serving 200
100-channel feeds peaked at 220 MiB before and 218 MiB after, and the mixed
20 000-request workload at 220 and 223 MiB (run-to-run noise is about
±10 MiB). The Python objects a feed no longer builds were short-lived, and a
worker's RSS is dominated by the interpreter with its imports (85 MiB) and the
in-process cache. The memory win of this roadmap comes from stage 6 (fewer
interpreters, one cache per server), not from moving individual pipelines.

What is left of a feed in Python is resolving the pages (the cache lookups,
whose L1 reads still hold the GIL for tens of µs each) and the popularity
update. The rest of the CPU is the ranker.
