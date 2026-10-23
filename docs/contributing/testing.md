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

`tests/test_graphql_contract.py` does the same for GraphQL: the full schema
(SDL) in `tests/snapshots/graphql/schema.graphql`, a snapshot per query, and
parity checks that every GraphQL result equals the matching REST response.

### Changing the output on purpose

```bash
make snapshots                 # UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py tests/test_graphql_contract.py
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
| `test_cache.py` | Memory LRU with memory limit, compression and expiry, Redis backend, circuit breaker |
| `test_memory_trim.py` | Periodic `malloc_trim` (glibc only), disabling it |
| `test_coalesce.py` | Single-flight sharing, error propagation, cancellation safety |
| `test_popularity.py` | Popularity decay and leader election on Redis (fakeredis with Lua) |
| `test_services.py` | Page and response caches, previews, stale-while-revalidate feed, refresher scheduling |
| `test_parsing.py` | The native page parser seen from Python: entities, media, forwards, broken-post isolation |
| `test_feed_ranking.py` | Feed signals and the native ranker: relative engagement, freshness, duplicates, topics, diversity |
| `test_graphql.py` | GraphQL schema shape, resolvers, errors |
| `test_graphql_limits.py` | GraphQL depth, cost, alias, token and batch limits |
| `test_api.py` | Validation, feature flags, headers, rate limiting, CORS, docs |
| `test_runtime.py` | Embedded refresher lifecycle, JSON logging |
| `test_docs_config.py` | Documentation translations: every page present, configuration in sync with English |

Tests are async-aware (`pytest-asyncio` in auto mode) and warnings are
errors.

## Memory

`tests/test_memory.py` (marker `memory`) sends thousands of requests straight
to the ASGI app over aliases of the recorded channels (`tests/workload.py`)
and fails when Python allocations or, on Linux, resident memory keep growing
after a warm-up, or when the memory cache exceeds its limit. It is part of the
default run, so CI runs it; skip it locally with `-m "not memory"`.

`scripts/memory_profile.py` prints the numbers behind it: import cost of the
main process and a worker, the size of one cached page, and RSS and Python
allocations sampled during a long workload:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 50
```

RSS is read from `/proc`, so run it on Linux for meaningful numbers.

## Live smoke test

`scripts/smoke_matrix.py` exercises a running instance against live t.me:

```bash
make up && make smoke
uv run --no-project python scripts/smoke_matrix.py --base-url https://api.example.com --preset auto
```

CI runs it against the freshly built image on every pull request.
