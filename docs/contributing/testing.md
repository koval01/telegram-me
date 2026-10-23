# Testing

```bash
make test        # ~160 tests, about 2 seconds, no network
```

The suite never talks to t.me. Two kinds of tests keep the service honest.

## Contract snapshots

`tests/test_snapshots.py` calls every endpoint against **recorded t.me
pages** and compares the complete JSON response with a file in
`tests/snapshots/`. Any change to what the API returns — a renamed field, a
different number, a missing post — fails a test and shows up as a readable
diff.

The recorded pages live in `tests/fixtures/telegram/` (gzip) with a
`manifest.json` describing which request each file answers. A
`FixtureFetcher` replaces the network layer and fails the test on any request
that was not recorded. Time is frozen with `time-machine`, because the feed
score depends on "now".

Coverage includes six channels (`durov`, `telegram`, `stranaua`, `tginfo`,
`nebudetafk`, `nebudetgg`), both parser modes, pagination in both directions,
previews, a missing channel, the feed, and individual posts with an inline
button, a poll, a sticker, a reply, a link preview and a voice message.

### Changing the output on purpose

```bash
make snapshots                 # UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py
git diff --stat tests/snapshots
git diff tests/snapshots        # review every change
```

### Recording new pages

```bash
make fixtures                          # re-record everything (changes many snapshots)
make fixtures CHANNELS="nebudetgg"     # add or refresh specific channels
```

Recording uses the production network stack. Re-recording replaces content
(views, new posts), so regenerate and review snapshots afterwards. To cover a
rare post type, add its ID to `EXTRA_POSTS` in `scripts/record_fixtures.py`.

## Unit tests

`tests/unit/` covers the pieces in isolation:

| File | Covers |
|---|---|
| `test_client.py` | URL building, status mapping, validation, the single network retry |
| `test_fetcher.py` | `ReqwestFetcher` against a real local HTTP server: gzip, headers, empty POST bodies |
| `test_cache.py` | Memory LRU with size limit and expiry, Redis backend, circuit breaker |
| `test_coalesce.py` | Single-flight sharing, error propagation, cancellation safety |
| `test_popularity.py` | Popularity decay and leader election on Redis (fakeredis with Lua) |
| `test_services.py` | Page and response caches, previews, stale-while-revalidate feed, refresher scheduling |
| `test_parsing.py` | Entities, media, forwards, broken-post isolation, scoring |
| `test_api.py` | Validation, feature flags, headers, rate limiting, CORS, docs |
| `test_runtime.py` | Embedded refresher lifecycle, JSON logging |

Tests are async-aware (`pytest-asyncio` in auto mode) and warnings are
errors.

## Live smoke test

`scripts/smoke_matrix.py` exercises a running instance against live t.me:

```bash
make up && make smoke
uv run --no-project python scripts/smoke_matrix.py --base-url https://api.example.com --preset auto
```

CI runs it against the freshly built image on every pull request.
