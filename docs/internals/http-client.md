# HTTP client

Every page the API serves starts as a request to t.me. Besides the headers, a
server sees two fingerprints of the client that sends it: the TLS
`ClientHello` (JA3, JA4) and the HTTP/2 connection preface (the Akamai
fingerprint: SETTINGS, WINDOW_UPDATE, priority and pseudo-header order). When
they name different clients than the `User-Agent`, the request is easier to
flag than one from either a real browser or an honest library.

## Two clients { #two-clients }

[`TELEGRAM_CLIENT`](../configuration.md#telegram_client) picks the `Fetcher`:

| | `browser` (default) | `reqwest` |
|---|---|---|
| Library | [wreq](https://crates.io/crates/wreq) 0.16 + [wreq-util](https://crates.io/crates/wreq-util) 0.2, in `rust/telegram-me-native` | [pyreqwest](https://pypi.org/project/pyreqwest/) |
| TLS | BoringSSL, as Chrome | rustls |
| HTTP/2 | the profile's SETTINGS, window and header order | hyper's |
| Headers | the profile's | the profile's |
| Connections | pooled HTTP/2, gzip/br/zstd/deflate | the same |

Both take their headers from one profile,
[`TELEGRAM_BROWSER_PROFILE`](../configuration.md#telegram_browser_profile)
on [`TELEGRAM_BROWSER_PLATFORM`](../configuration.md#telegram_browser_platform)
(`chrome_149` on `android` by default): the `User-Agent`, `sec-ch-ua*`,
`accept`, the fetch metadata and their order. No header is written by hand,
so they can not contradict each other. Only `browser` also has the handshake
of that browser.

The client sends what a browser on the channel page sends:

- a **navigation** (`GET /s/<channel>`, `GET /<channel>`): the profile's
  headers with `accept-language: ru`;
- the **"load more" XHR** (`POST /s/<channel>?before=…`): the page's jQuery
  call, with `x-requested-with`, `accept: application/json, …`,
  `sec-fetch-mode: cors`, `sec-fetch-site: same-origin`, `origin`, `referer`,
  `priority: u=1, i` and Chromium's header order for it, captured from a
  Chromium 152 browser (Electron).

## Fingerprints { #fingerprints }

Measured on 2026-10-24 against `https://tls.peet.ws/api/all` with
`scripts/fingerprint.py` (three connections per row). The reference row is a
Chromium 152 browser (Electron, BoringSSL like Chrome) measured the same day
with the same service. A Google Chrome build was not measured.

| Client | JA4 | Akamai HTTP/2 fingerprint | Akamai hash |
|---|---|---|---|
| `reqwest` before this change (hand-written Chrome 141 headers) | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` |
| `reqwest` (profile headers) | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` |
| `browser` (`chrome_149`, `android`) | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` |
| Chromium 152 (reference) | `t13d1516h2_8daaf6152771_806a8c22fdea` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` |

- **`reqwest`** offers 10 cipher suites and 11 extensions, no GREASE, no
  ALPS or certificate compression: a rustls handshake under a Chrome
  `User-Agent`. Its HTTP/2 preface is hyper's (a 2 MiB window, pseudo-headers
  `m,s,a,p`), which Chrome never sends.
- **`browser`** has Chrome's 15 cipher suites and 16 extensions (GREASE,
  `X25519MLKEM768`, ALPS, brotli certificate compression, ECH GREASE) and
  Chrome's HTTP/2 preface. JA3 hashes differ on every connection, as Chrome's
  do (it permutes its extensions), so they are not compared; JA4 sorts them.
- **The reference** matches `browser` on the cipher part of JA4 and on the
  whole HTTP/2 fingerprint. The last part of JA4 differs: Chromium 152 also
  offers three ML-DSA signature algorithms (`0x0904`–`0x0906`) that the
  `chrome_149` profile, three releases older, does not. The profile is the
  newest that wreq-util ships today.

Header order of a navigation, after the pseudo-headers:

- `reqwest` before: `accept, accept-language, cache-control, dnt, priority,
  sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform, sec-fetch-dest,
  sec-fetch-mode, sec-fetch-site, sec-fetch-user, upgrade-insecure-requests,
  user-agent, accept-encoding` — alphabetical, with `sec-ch-ua-platform:
  Android` unquoted;
- `browser` and `reqwest` now: `sec-ch-ua, sec-ch-ua-mobile,
  sec-ch-ua-platform, upgrade-insecure-requests, user-agent, accept,
  sec-fetch-site, sec-fetch-mode, sec-fetch-user, sec-fetch-dest,
  accept-encoding, accept-language, priority` — Chrome's order; the
  reference sends the same order (Electron leaves out `sec-ch-ua*` on
  navigations).

The "load more" XHR from `browser` and from the reference:
`content-length, sec-ch-ua-platform, x-requested-with, user-agent, accept,
sec-ch-ua, sec-ch-ua-mobile, origin, sec-fetch-site, sec-fetch-mode,
sec-fetch-dest, referer, accept-encoding, accept-language, priority`.
`reqwest` sends the navigation headers with `x-requested-with` and
`content-length` appended.

## Performance and memory { #performance }

Against live t.me from one machine (12 sequential page loads, then 16 at once):

| | First request | Warm p50 | 16 in parallel | Peak RSS of the process |
|---|---|---|---|---|
| `reqwest` | 246–296 ms | 98–121 ms | 172 ms\* | 58 MiB |
| `browser` | 180–281 ms | 93–105 ms | 160–178 ms | 60–61 MiB |

\* one of two runs took 2 s for the parallel batch; t.me was slow, not the client.

In the Docker image with `ENABLE_FEED=true`, after the smoke matrix, workers
held 105–108 MiB RSS with `browser` and 117–128 MiB with `reqwest`. The
`browser` requests run on a shared tokio runtime of two worker threads.

## Async bridge { #async-bridge }

`BrowserClient.fetch` returns an asyncio future built by
[pyo3-async-runtimes](https://crates.io/crates/pyo3-async-runtimes): the
request runs on the tokio runtime without the GIL; when it ends, a blocking
thread takes the GIL once and sets the result through the event loop's
`call_soon_threadsafe`. Cancelling the asyncio task drops the Rust future,
and with it the request. The crate is the maintained PyO3 bridge, so the
module does not keep its own loop integration.

## Build { #build }

BoringSSL is C and C++ built with cmake (`btls-sys`); bindgen needs libclang,
and the crate applies its patches with git. The Docker build stage installs
`g++ make cmake clang libclang-dev git`; the runtime image gets none of them.

| | Cold build, arm64 (native) | Cold build, amd64 (emulated on arm64) | Image |
|---|---|---|---|
| before | 121 s | 172 s | 317.1 MB (arm64), 69.7 MB (amd64) |
| with wreq | 196 s | 386 s | 324.4 MB (arm64), 72.2 MB (amd64) |

In CI, arm64 is built under QEMU, as `release.yml` builds it. A cold build of
that image on `ubuntu-latest` took 17 min 26 s on main and 28 min 12 s with
wreq (+10 min 46 s). The release job builds both platforms in one run, so a
release that recompiles the crate takes about that much longer. The layer
cache spares releases that do not touch `rust/`.

## Keeping the profile current { #updates }

Chrome ships every four weeks; a profile ages with it. Dependabot updates
`wreq` and `wreq-util` with the other cargo crates. When a newer Chrome
profile appears, update the default of `TELEGRAM_BROWSER_PROFILE` (settings,
`DEFAULT_BROWSER_PROFILE` in `telegram/client.py`, the three
`configuration.md` files, `.env.example`) and run
`scripts/fingerprint.py` to compare:

```bash
uv run python scripts/fingerprint.py --runs 3
```
