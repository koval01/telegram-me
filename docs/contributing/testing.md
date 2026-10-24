# Testing

```bash
make test          # ~550 tests, about 30 seconds, no network
make rust          # Rust: fmt, clippy, tests (parsers on hostile HTML included)
make integration   # the built image as two servers with Valkey (Docker)
```

The default suite never talks to t.me. Every merge to `main` is deployed to
production, so these tests and the [CI gates](#ci-jobs) are what stands
between a broken commit and users.

## Contract snapshots

`tests/test_snapshots.py` calls every endpoint against **recorded t.me
pages** and compares the complete JSON response with a file in
`tests/snapshots/`. Any change to what the API returns — a renamed field, a
different number, a missing post — fails a test and shows up as a readable
diff. The JSON files are compared parsed, so each response's exact bytes are
pinned as well, by their SHA-256 in `tests/snapshots/raw_digests.json`: key
order, number formatting and string escaping must not change either, which is
what lets code move from pydantic to Rust without touching the contract.

The recorded pages live in `tests/fixtures/telegram/` (gzip) with a
`manifest.json` describing which request each file answers. A
`FixtureFetcher` replaces the network layer and fails the test on any request
that was not recorded. Time is frozen with `time-machine`, because the feed
score depends on "now".

Coverage includes six channels (`durov`, `telegram`, `stranaua`, `tginfo`,
`nebudetafk`, `nebudetgg`), both parser modes, pagination in both directions,
previews, a missing channel, the feed, and individual posts with an inline
button, a poll, a sticker, a reply, a link preview and a voice message. A
synthetic feed, seeded straight into the cache, adds collapsed duplicates and
pages the parser would never produce (no username, no channel info, posts
without footer or content).

`tests/test_graphql_contract.py` does the same for GraphQL: the full schema
(SDL) in `tests/snapshots/graphql/schema.graphql`, a snapshot per query, and
parity checks that every GraphQL result equals the matching REST response.
`tests/test_openapi_contract.py` pins the REST contract as a whole, the
OpenAPI document with every feature on, in `tests/snapshots/openapi.json`.

### Changing the output on purpose

```bash
make snapshots                 # UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py tests/test_graphql_contract.py tests/test_openapi_contract.py
git diff --stat tests/snapshots
git diff tests/snapshots        # review every change
```

### Breaking changes

A snapshot only shows that the contract changed. `scripts/api_contract.py`
decides whether the change breaks clients, by comparing `openapi.json` and
`schema.graphql` with the base of the pull request, which is what production
serves:

```bash
make contract                  # against origin/main
```

Breaking: an operation, field or enum value removed, a response field that
may now be missing or have another type (`null` included), and anything that
rejects requests accepted before (a new required parameter or field, a
narrower type, a lower limit). Additions are compatible. The GraphQL schema
is compared by graphql-core (`find_breaking_changes`). The **Guards** workflow
fails a pull request with a breaking change unless it is labelled
`breaking-change`, which means a major version (see
[Releasing](releasing.md)).

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
| `test_cache.py` | In-process cache (limit, compression, expiry), Redis backend, circuit breaker, two-level reads, fills, outage |
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
| `test_api_contract.py` | Which contract changes `scripts/api_contract.py` reports as breaking |
| `test_properties.py` | Property-based tests, see [below](#property-and-fuzz-tests) |

Tests are async-aware (`pytest-asyncio` in auto mode) and warnings are
errors.

## Property-based and fuzz tests { #property-and-fuzz-tests }

Every t.me page is input the service does not control, and the native
extension is built with `panic = "abort"`: a panic in a parser takes the
whole worker process down. Generated input looks for what hand-written cases
miss.

- `tests/unit/test_properties.py` (hypothesis): whatever page t.me returns
  (recorded pages with parts deleted, repeated, cut off or spliced in), every
  endpoint answers 200, 404 or 502, never 500; the parsing wrappers fail only
  with `TelegramError`; the protection middleware answers any `X-PoW` header,
  path and method with a well-formed response, accepts a valid proof exactly
  once and counts the rate limit exactly; the feed holds each post once, only
  from the requested channels, whatever their order. Examples come from a
  fixed seed; `HYPOTHESIS_PROFILE=nightly` searches longer with random ones.
- `rust/telegram-me-native/tests/hostile_html.rs` (`cargo test`): every
  recorded page whole and with seeded mutations, soup of t.me markup and
  absurd values, deeply nested markup on a thread with a 1 MiB stack, and
  parse times that must grow linearly (4× the input in under 9× the time) for
  many posts, entities, unclosed tags and look-ahead patterns.
  `HOSTILE_CASES` and `HOSTILE_SEED` make it run longer or differently.
- `rust/telegram-me-native/fuzz/` (cargo-fuzz, nightly Rust): a
  coverage-guided fuzzer over all page parsers, seeded with the recorded
  pages:

    ```bash
    python3 scripts/fuzz_corpus.py rust/telegram-me-native/fuzz/corpus/pages
    cd rust/telegram-me-native/fuzz && cargo +nightly fuzz run -s none pages corpus/pages -- -max_total_time=60
    ```

## Memory

`tests/test_memory.py` (marker `memory`) sends thousands of requests straight
to the ASGI app over aliases of the recorded channels (`tests/workload.py`)
and fails when Python allocations or, on Linux, resident memory keep growing
after a warm-up, or when the in-process cache exceeds its limit. It is part of
the default run, so CI runs it; skip it locally with `-m "not memory"`. The
cache's entries live on the Rust heap, which `tracemalloc` does not see: that
the limit counts what they really hold is checked by
`rust/telegram-me-native/tests/cache_memory.rs` with a counting allocator.

`scripts/memory_profile.py` prints the numbers behind it: import cost of the
main process and a worker, the size of one cached page, and RSS and Python
allocations sampled during a long workload:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 50
```

RSS is read from `/proc`, so run it on Linux for meaningful numbers.

## Integration tests

`tests/integration/` runs the built image as it runs in production, which the
tests above can only imitate with fakeredis or inside one process:
`tests/integration/compose.yaml` starts two servers with two granian workers
each, sharing a real Valkey, in front of a fake t.me
(`tests/integration/fake_telegram.py` serves the recorded pages and counts
requests; the servers reach it through [`TELEGRAM_BASE_URL`](../configuration.md#telegram_base_url)).

| Test | Shows |
|---|---|
| `test_second_server_reads_the_first_servers_cache` | the two-level cache: a page fetched by one server is served by the other from Redis |
| `test_concurrent_misses_on_both_servers_fetch_once` | the fetch lease: 24 concurrent misses on 4 processes, one request to t.me |
| `test_shared_rate_limit_counts_a_client_on_every_server` | `RATE_LIMIT_SHARED`: a client is limited although no process alone sees enough requests |
| `test_proof_of_work_from_one_server_is_accepted_by_the_other` | a challenge from one server solves a request to the other; replayed, forged |
| `test_rolling_upgrade.py` | the previous release (`TME_PREVIOUS_IMAGE`) and the new one read each other's cache entries |
| `test_valkey_outage.py` | Valkey stops: both servers keep answering; it returns: they share again |

```bash
make docker
TME_TEST_IMAGE=telegram-me:dev TME_PREVIOUS_IMAGE=docker.io/koval01/telegram-me:latest make integration
```

They only talk HTTP and Docker, so they run without the project installed
(`uv run --only-group integration`) and skip without `TME_TEST_IMAGE`.

## Deploy scripts

`tests/deploy/test_remote_deploy.py` runs `deploy/remote-deploy.sh` on the
local Docker as on a production server (`deploy/compose.prod.yaml`, Valkey
and the fake t.me reached through the stack's gateway): the upgrade from the
previous release, a release that never becomes healthy, a release that is
healthy but fails every page request (only the smoke test notices), the
canary check (`--verify`), `--rollback`, and the `proxy` profile with Caddy.
`tests/deploy/test_rollout.py` runs `deploy/rollout.sh`, the fleet logic of
the deploy workflow, with stub `ssh` and `scp`: canary first, every server
that took a release rolled back when a later one, the canary or the public
smoke test fails. It needs bash 4.4 or later and is part of `make test`.

```bash
TME_TEST_IMAGE=telegram-me:dev make deploy-test
```

## Coverage floors

CI fails when line coverage drops below a floor: `fail_under` in
`pyproject.toml` for Python, `[package.metadata.coverage] fail-under-lines`
in `rust/telegram-me-native/Cargo.toml` for Rust (`cargo llvm-cov`). The
**Guards** workflow fails a pull request that lowers a floor
(`scripts/coverage_floor.py`): floors only go up, together with the tests that
earn them.

## Live smoke test

`scripts/smoke_matrix.py` exercises a running instance against live t.me.
Besides status codes it checks the content of the main responses (channel
identity, complete posts, pagination on the right side, feed scores): a
change of the t.me markup usually still answers 200, just with empty fields.

```bash
make up && make smoke
uv run --no-project python scripts/smoke_matrix.py --base-url https://api.example.com --preset auto
```

It uses only the standard library and only pages that are recorded too, so
it also passes against the fake t.me. CI runs it against the freshly built
image on every pull request, every deployment runs it on every server and
through the public URL (see [Production](../deployment/production.md#how-a-deployment-works)),
and the **Nightly** workflow runs it against live t.me and production.

## CI jobs { #ci-jobs }

| Workflow, job | When | Takes | Catches |
|---|---|---|---|
| CI: Rust | every push | ~3 min | fmt, clippy, Rust tests (hostile HTML), Rust coverage floor |
| CI: Lint & types | every push | ~3.5 min | ruff, mypy, shellcheck, actionlint |
| CI: Tests | every push | ~2 min | the suite above on Python 3.13 and 3.14, Python coverage floor |
| CI: Docker image smoke test | after the three above | ~2 min | the image starts hardened, smoke matrix against live t.me, graceful stop |
| CI: Integration | after the image | ~2 min | [integration tests](#integration-tests) |
| CI: Deploy scripts | after the image | ~2 min | [deploy script tests](#deploy-scripts) |
| Guards | every pull request, label changes | ~10 s | [breaking API changes](#breaking-changes), lowered coverage floors |
| Nightly | 03:23 UTC daily | ~15 min | live t.me and production smoke tests, 10 min of fuzzing, long property runs; opens an issue labelled `nightly` |

Release (pushes to `main`) runs CI and Guards before the image is built and
deployed.
