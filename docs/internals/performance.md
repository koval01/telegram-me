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
| Container memory, 1 worker | — | ~140 MB |

## Reproducing

```bash
uv run python scripts/benchmark.py   # in-process, recorded pages, no network
```

For server throughput, start an instance and point a load generator at it:

```bash
make up                              # API + Valkey on :8000
wrk -t2 -c64 -d10s --latency http://127.0.0.1:8000/healthz
```
