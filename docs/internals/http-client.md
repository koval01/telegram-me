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
| HTTP/2 | the profile's SETTINGS, window, priority and header order | hyper's |
| Headers | the profile's | the profile's |
| Cookies | kept per worker and sent back ([Cookies](#cookies)) | none |
| Connections | pooled HTTP/2, gzip/br/zstd/deflate | the same |

Production uses `browser`; `reqwest` is not used there.

Both take their headers from one profile,
[`TELEGRAM_BROWSER_PROFILE`](../configuration.md#telegram_browser_profile)
on [`TELEGRAM_BROWSER_PLATFORM`](../configuration.md#telegram_browser_platform)
(`chrome_156` on `android` by default): the `User-Agent`, `sec-ch-ua*`,
`accept`, the fetch metadata and their order. No header is written per
request, so they can not contradict each other. Only `browser` also has the
handshake of that browser.

The client sends what a browser on the channel page sends:

- a **navigation** (`GET /s/<channel>`, `GET /<channel>`): the profile's
  headers with `accept-language: ru`;
- the **"load more" XHR** (`POST /s/<channel>?before=…`): the page's jQuery
  call, with `x-requested-with`, `accept: application/json, …`,
  `sec-fetch-mode: cors`, `sec-fetch-site: same-origin`, `origin`, `referer`,
  `priority: u=1, i` and Chromium's header order for it, captured from a
  Chromium 152 browser (Electron).

## Our Chrome profile { #profile }

Chrome now ships every two weeks; wreq-util's newest profile, `chrome_149`,
is seven releases behind the stable channel. On 2026-10-25 the stable
channel was Chrome 156 (full rollout on 2026-10-20; 157 was rolling out to a
few percent, full on 2026-11-03). `chrome_156` is our own profile of it, built
on wreq's emulation API in `rust/telegram-me-native/src/http/chrome.rs`:

- **TLS**: Chrome's 15 cipher suites; groups `X25519MLKEM768`, `X25519`,
  P-256, P-384 with key shares for the first two; GREASE; extension
  permutation; ALPS for `h2` on the new codepoint (17613); brotli certificate
  compression; ECH GREASE; OCSP and SCT requests; PSK resumption.
- **HTTP/2**: `SETTINGS` 1:65536, 2:0, 4:6291456, 6:262144, a
  `WINDOW_UPDATE` of 15663105, pseudo-headers `m,a,s,p`, and the `HEADERS`
  priority Chrome sends: exclusive on stream 0, **weight 256**. wreq-util's
  Chrome profiles send weight 220, which Chrome does not.
- **Headers**: Chrome's order; `User-Agent` `Chrome/156.0.0.0` (the reduced
  form: `Linux; Android 10; K` on Android); `sec-ch-ua` with the GREASE brand
  and brand order Chromium derives from the major version (for 156:
  `"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"`);
  `image/jxl` in `accept`, which Chrome sends since it decodes JPEG XL (155).
- `ios` is refused: Chrome on iOS is WebKit on Apple's TLS stack, so a
  BoringSSL handshake would contradict its `User-Agent`.

### What BoringSSL can not send { #boringssl-limits }

Chrome 155 also sends three things that wreq can not, because of the
BoringSSL it builds (`btls-sys` 0.5.6, BoringSSL API version 37; wreq 0.16.1
and btls 0.5.6 are the newest releases):

- **ML-DSA signature algorithms** `0x0904`, `0x0905`, `0x0906`. Their names
  are missing from BoringSSL's signature algorithm table, and
  `set_sigalg_prefs` rejects any codepoint that is not in it: a client built
  with `mldsa65` in its list fails with `INVALID_SIGNATURE_ALGORITHM`. wreq
  only takes the list as names, so the codepoints can not be passed raw
  either. That also settles the safety question: a server can never pick an
  ML-DSA algorithm we could not verify, because we can not offer one. t.me
  serves classic (ECDSA) certificates anyway.
- **A GREASE value in `signature_algorithms`** (Chrome sends one first; this
  BoringSSL greases ciphers, groups, versions and extensions only).
- **The `trust_anchors` extension** (`0xca34`, TLS Trust Anchor IDs), with
  the IDs of the Chrome Root Store. The C library has it
  (`SSL_CTX_set1_requested_trust_anchors`), but neither btls nor wreq exposes
  it, and the crate forbids `unsafe` code. Electron (Chromium 152) does not
  send it either; whether Chrome on Android does was not measured.

These leave the last part of JA4 different from Chrome's. Closing the gap
needs a btls release with a newer BoringSSL and an API for trust anchors.

## Fingerprints { #fingerprints }

Measured on 2026-10-25 against `https://tls.peet.ws/api/all` with
`scripts/fingerprint.py` (three connections per row, each a full handshake).
The references were measured the same day with the same service: Google
Chrome 155.0.8059.85 on macOS (headless, a fresh profile) and Chromium 152 in
Electron (the desktop app's browser pane). No Chrome 156 for Android was
available to capture.

| Client | JA4 | Akamai HTTP/2 fingerprint | Akamai hash | `HEADERS` weight |
|---|---|---|---|---|
| `reqwest` | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` | — |
| `browser`, `chrome_149` | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 220 |
| `browser`, `chrome_156` | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 256 |
| Chrome 155 (reference) | `t13d1517h2_8daaf6152771_cb7bf5808d99` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 256 |
| Chromium 152, Electron (reference) | `t13d1516h2_8daaf6152771_806a8c22fdea` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 256 |

- **`reqwest`** offers 10 cipher suites and 11 extensions, no GREASE, no
  ALPS or certificate compression: a rustls handshake under a Chrome
  `User-Agent`. Its HTTP/2 preface is hyper's (a 2 MiB window, pseudo-headers
  `m,s,a,p`), which Chrome never sends.
- **`browser`** has Chrome's 15 cipher suites, 16 extensions and HTTP/2
  preface; the cipher part of JA4 (`8daaf6152771`) and the Akamai hash match
  both references. JA3 hashes differ on every connection, as Chrome's do (it
  permutes its extensions), so they are not compared; JA4 sorts them.
- **The last part of JA4** hashes the extensions and the signature
  algorithms. Chrome 155 has `0xca34` among its 17 extensions and starts its
  signature algorithms with `0x0904,0x0905,0x0906`; Chromium 152 has the
  same 16 extensions as `browser` plus the ML-DSA algorithms. Both are the
  [BoringSSL limits](#boringssl-limits) above, so `chrome_156` keeps the
  handshake of `chrome_149`.
- **Groups and key shares** are the same as Chrome's: GREASE,
  `X25519MLKEM768`, `X25519`, P-256, P-384, with shares for GREASE,
  `X25519MLKEM768` and `X25519`.

Header order of a navigation, after the pseudo-headers, from `browser` and
from Chrome 155: `sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform,
upgrade-insecure-requests, user-agent, accept, sec-fetch-site,
sec-fetch-mode, sec-fetch-user, sec-fetch-dest, accept-encoding,
accept-language, priority`. `reqwest` sends the same order (pseudo-headers
`m,s,a,p`).

The "load more" XHR from `browser` and from Chromium 152:
`content-length, sec-ch-ua-platform, x-requested-with, user-agent, accept,
sec-ch-ua, sec-ch-ua-mobile, origin, sec-fetch-site, sec-fetch-mode,
sec-fetch-dest, referer, accept-encoding, accept-language, priority`.
`reqwest` sends the navigation headers with `x-requested-with` and
`content-length` appended.

With a cookie, Chromium sends `cookie` between `accept-language` and
`priority`, on navigations and XHRs alike; `browser` does the same.

## Cookies { #cookies }

What t.me sets, checked on 2026-10-25:

- **One cookie, `stel_ssid`**, on every kind of request we send: the channel
  page `/s/<channel>`, the "load more" `POST` and the preview `/<channel>`.
  Its attributes are `path=/; samesite=None; secure; HttpOnly`, and it
  expires 24 hours after it was issued.
- Sent back, it is **not set again**, so its expiry does not slide. An
  unknown value is replaced with a new one, so t.me checks it.
- Without cookies every request looked like a new visitor: t.me issued a new
  `stel_ssid` to each.
- t.me also **writes the value into its pages**, in the "Download Telegram"
  links (`telegram.org/dl?tme=<value>`). The parser does not keep those links;
  `tests/unit/test_session_privacy.py` checks that no recorded token reaches
  the parsed pages (what the cache stores) or any response.

The `browser` client keeps a cookie jar (wreq's RFC 6265 jar, which sorts
cookies as Chromium does) and sends the cookies back. A **session** is one
browser identity: the jar, the pooled connections and their TLS session
tickets. The client replaces it with a new one, empty jar and new connections,
when the first of these happens:

- [`TELEGRAM_SESSION_LIFETIME`](../configuration.md#telegram_session_lifetime)
  passes, ±25 % at random so that the workers of a fleet do not all start
  over at once (default 4 hours, so 3 to 5);
- its cookies have expired, or t.me deleted them;
- t.me answers a 4xx other than 404 (a 403 or 429 may mean the identity was
  flagged; 404 is a channel that does not exist).

Requests in flight finish on the session they started on; its connections
close when the last of them ends. A new session's first requests, sent at once
(a feed refresh), go out without a cookie and may each get a `stel_ssid`; the
jar keeps the last, as a browser opening several tabs at once would.

**One identity per worker process**, not one shared through Redis or the
peer layer. t.me attaches nothing to the cookie that we would want to share:
the pages are the same with or without it. One cookie shared by every worker
of a fleet would be one "browser" speaking from several IP addresses at
once, with as many TLS sessions and many times a browser's request rate,
which is less like a browser, not more; and it would add a Redis round trip
to requests for nothing. Each worker is one browser, as each would be on its
own machine.

Cookies never leave the native client: `fetch` returns only the status and
the body, the client's `Debug` output leaves out the jar, callers can not set
a `cookie` header, and no log line carries one. The Rust tests
(`tests/http_client.rs`) cover keeping and sending cookies, their place in
the header order, each way a session ends, and that cookies stay inside.

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

Chrome ships every two weeks, and between releases its handshake rarely
changes. To follow a new stable Chrome, bump `MAJOR` and `NAME` in
`rust/telegram-me-native/src/http/chrome.rs` (the `User-Agent` and
`sec-ch-ua` follow), then the default of `TELEGRAM_BROWSER_PROFILE`
(settings, `DEFAULT_BROWSER_PROFILE` in `telegram/client.py`, the three
`configuration.md` files, `.env.example`) and the tests that name it. Compare
with a real Chrome on the same echo service before changing anything else:

```bash
uv run python scripts/fingerprint.py --runs 3
```

Dependabot updates `wreq` and `wreq-util` with the other cargo crates. When a
btls release can offer ML-DSA or trust anchors, add them to the profile and
remeasure.
