# Rate limit and proof of work

Two optional protections a server owner can switch on: a per-client **rate
limit** and **proof of work** (PoW). Both are off by default. Both are checked
in native code before routing, so a rejected request never reaches
validation, the cache or t.me. A check takes well under a microsecond.

The rate limit runs first, then proof of work. Requests rejected for a missing
or wrong proof still count against the rate limit, which bounds how fast
anyone can probe.

## Rate limit

Enable it with [`ENABLE_RATE_LIMIT`](../configuration.md#enable_rate_limit).

- **Algorithm.** Each client IP may make
  [`RATE_LIMIT_BURST`](../configuration.md#rate_limit_burst) requests at once
  (by default [`RATE_LIMIT_RPM`](../configuration.md#rate_limit_rpm)), and then
  one more every `60 / RATE_LIMIT_RPM` seconds. This is GCRA, the generic cell
  rate algorithm. Its only state per client is one timestamp, so memory stays
  flat however fast a client sends.
- **Across workers and servers.** With Redis and
  [`RATE_LIMIT_SHARED`](../configuration.md#rate_limit_shared) (the default),
  every process adds its accepted requests to a per-minute counter in Redis once
  a second. A client over the limit there is blocked everywhere until the
  minute ends. Requests never wait for Redis; the price is that a client can go
  over the shared limit by up to one second of traffic per process. Without
  Redis each process limits on its own.
- **Client address.** Behind a reverse proxy, set
  [`FORWARDED_ALLOW_IPS`](../configuration.md#forwarded_allow_ips) so the
  client IP comes from `X-Forwarded-For`, or every client shares the proxy's
  limit.

Every response carries:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | `RATE_LIMIT_RPM` |
| `X-RateLimit-Remaining` | Requests the client may still make right away |

Over the limit the API answers `429`:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0
Content-Type: application/json

{"detail":"Rate limit exceeded"}
```

`Retry-After` is the number of seconds until the next request is allowed.

## Proof of work

Enable it with [`ENABLE_POW`](../configuration.md#enable_pow) and a
[`POW_SECRET`](../configuration.md#pow_secret) of at least 32 characters, the
same on every server. Each request must then carry a hash that took the client
work to find and takes the server microseconds to check. Scraping the API
through someone else's instance gets expensive, and flooding it gets more
expensive still.

### How a client answers

1. **Get a challenge.** Call `GET /v1/pow/challenge`, or take one from any
   rejection (see below):

    ```json
    {
      "challenge": "1.1792860000.18.3q2-7w8_aBcDeFgHiJkL.Zm9vYmFyYmF6cXV4cXV1eA",
      "difficulty": 18,
      "expires": 1792860000,
      "algorithm": "sha256",
      "header": "X-PoW",
      "input": "{challenge}\\n{nonce}\\n{METHOD} {target}"
    }
    ```

2. **Solve it for the request.** Find a `nonce` (1–64 characters of
   `A–Z a–z 0–9 - _`) such that

    ```text
    SHA-256(challenge + "\n" + nonce + "\n" + METHOD + " " + target)
    ```

    starts with `difficulty` zero bits. `target` is the request path and query
    exactly as sent, for example `/v1/body/durov?position=5`. On average this
    takes `2^difficulty` hashes.

3. **Send it** in the `X-PoW` header as `challenge:nonce`.

Reuse the challenge for every request until it expires
([`POW_CHALLENGE_TTL`](../configuration.md#pow_challenge_ttl), 5 minutes by
default), with a new nonce for each request. A solution is accepted once: it
is tied to its method and target, and each server process remembers the
solutions it has accepted until their challenge expires.

=== "Python"

    ```python
    import hashlib
    import itertools

    import requests

    API = "https://api.example.com"


    def solve(challenge: str, difficulty: int, method: str, target: str) -> str:
        prefix = hashlib.sha256(f"{challenge}\n".encode())
        suffix = f"\n{method} {target}".encode()
        for n in itertools.count():
            nonce = format(n, "x")
            digest = prefix.copy()
            digest.update(nonce.encode() + suffix)
            value = int.from_bytes(digest.digest(), "big")
            if value >> (256 - difficulty) == 0:
                return nonce
        raise AssertionError


    pow_ = requests.get(f"{API}/v1/pow/challenge", timeout=10).json()
    target = "/v1/body/durov"
    nonce = solve(pow_["challenge"], pow_["difficulty"], "GET", target)
    response = requests.get(API + target, headers={"X-PoW": f"{pow_['challenge']}:{nonce}"}, timeout=30)
    ```

=== "JavaScript"

    ```javascript
    const API = "https://api.example.com";

    async function solve(challenge, difficulty, method, target) {
      const encoder = new TextEncoder();
      for (let n = 0; ; n++) {
        const nonce = n.toString(16);
        const data = encoder.encode(`${challenge}\n${nonce}\n${method} ${target}`);
        const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
        let bits = 0;
        for (const byte of hash) {
          if (byte === 0) { bits += 8; continue; }
          bits += Math.clz32(byte) - 24;
          break;
        }
        if (bits >= difficulty) return nonce;
      }
    }

    const pow = await (await fetch(`${API}/v1/pow/challenge`)).json();
    const target = "/v1/body/durov";
    const nonce = await solve(pow.challenge, pow.difficulty, "GET", target);
    const response = await fetch(API + target, {
      headers: { "X-PoW": `${pow.challenge}:${nonce}` },
    });
    ```

    `crypto.subtle` is asynchronous, so each hash costs an `await`. For high
    difficulties use a synchronous SHA-256 implementation in a Web Worker.

### Rejections

A request without a valid proof gets a JSON error with a **fresh challenge**,
so a client can solve it and retry without an extra round trip:

```json
{
  "detail": "Proof of work required",
  "pow": {
    "code": "missing",
    "challenge": "1.1792860000.18.…",
    "difficulty": 18,
    "expires": 1792860000,
    "algorithm": "sha256",
    "header": "X-PoW",
    "input": "{challenge}\\n{nonce}\\n{METHOD} {target}"
  }
}
```

The same challenge is also sent in the `X-PoW-Challenge`, `X-PoW-Difficulty`
and `X-PoW-Expires` headers. These headers, `Retry-After` and the rate-limit
headers are exposed to browsers through CORS.

| Status | `code` | Meaning |
|---|---|---|
| `428` | `missing` | No `X-PoW` header |
| `428` | `expired` | The challenge has expired |
| `428` | `too_easy` | The challenge asks for less work than the server now requires |
| `400` | `malformed` | Not `challenge:nonce`, or a field out of range |
| `403` | `forged` | The challenge was not issued by this server, or was issued to another IP |
| `403` | `insufficient_work` | The hash lacks zero bits, usually because the method or target differs from what was hashed |
| `403` | `replayed` | This solution was already used |

### No proof needed

Some requests are served without a proof: the pages a browser loads before
it can solve one (the landing page `/`, `/api`, `/openapi.json`, `/docs/…`
and `/static/…`), `/robots.txt`, `/sitemap.xml`, `/healthz`,
`/v1/pow/challenge` itself, the GraphiQL page (`GET /graphql` without a
query) and CORS preflights (`OPTIONS`). The rate limit counts those pages
like any other request; it skips only requests from the server itself, made
over loopback and not forwarded by a proxy (health checks, the deploy's smoke
test). A forwarded `X-Forwarded-For: 127.0.0.1` is not local. GraphQL
queries (`POST /graphql`) need a proof like any other request, with
`POST /graphql` as method and target.

### Security notes

- **Challenges are stateless.** A challenge is
  `1.<expires>.<difficulty>.<salt>.<tag>`, where `tag` is an HMAC-SHA256 of the
  other fields (and of the client IP with
  [`POW_BIND_IP`](../configuration.md#pow_bind_ip)). Issuing one takes no
  storage, so asking for challenges cannot exhaust the server. The challenge
  endpoint is rate limited like everything else.
- **Checks run cheapest first:** size (the header may be at most 256 bytes),
  syntax, expiry, difficulty, the tag (a constant-time comparison), the hash,
  then the replay cache. Only proofs that are actually valid ever reach the
  cache.
- **Raising the difficulty** takes effect at once: easier challenges are
  rejected with `too_easy`, and the response carries a new one.
- **Replays.** Each process remembers accepted solutions until their challenge
  expires, up to 500 000. Across processes, a solution could be used once per
  process, but only for the exact method and target it was solved for, whose
  response is usually already cached.
- **IP binding.** It stops a farm from solving challenges centrally and
  handing them out. Turn it off only if clients change address within
  `POW_CHALLENGE_TTL`, for example mobile networks or IPv6 privacy addresses.
- **Request bodies are not hashed.** `POST /v1/feed` and `POST /v1/previews`
  bind to their target only. The single-use rule still makes every request
  cost one solution.

### Choosing the difficulty

The expected work is `2^difficulty` hashes per request, so each extra bit
doubles it.

| `POW_DIFFICULTY` | Native code (≈ 15 M hashes/s per core) | Python `hashlib` (≈ 1.5 M/s) |
|---|---|---|
| 16 | ~4 ms | ~45 ms |
| 18 (default) | ~17 ms | ~0.2 s |
| 20 | ~70 ms | ~0.7 s |
| 22 | ~0.3 s | ~3 s |

SHA-256 is cheap on GPUs. PoW raises the cost of abuse, but it does not stop
a determined attacker with hardware. Combine it with the rate limit.
