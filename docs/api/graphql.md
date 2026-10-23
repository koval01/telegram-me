# GraphQL

```http
POST /graphql
```

A GraphQL API next to the REST `/v1` API, for clients that want to choose the
fields they receive or combine several requests into one round trip. It is
backed by the same services as REST: same caching, request coalescing,
validation and feature flags, and the same data.

It is on by default; turn it off with
[`ENABLE_GRAPHQL=false`](../configuration.md#enable_graphql), after which
`/graphql` answers `404`.

## Endpoint

| Method | What |
|---|---|
| `POST /graphql` | `{"query": …, "variables": {…}, "operationName": …}` as JSON, or a JSON **array** of such objects (a batch, see [Limits](#limits)) |
| `GET /graphql` | In a browser: the GraphiQL IDE. With `?query=…&variables=…`: runs a query |

GraphiQL, `GET` queries and schema introspection are available while docs are
enabled; [`DISABLE_DOCS=true`](../configuration.md#disable_docs) turns all three
off (`POST` keeps working).

```bash
curl http://localhost:8000/graphql \
  -H 'content-type: application/json' \
  -d '{"query": "{ body(channel: \"durov\") { channel { title { string } } } }"}'
```

```json
{"data": {"body": {"channel": {"title": {"string": "Pavel Durov"}}}}}
```

## Schema

Every root field mirrors a REST endpoint and returns the same data:

| Field | Returns | REST equivalent |
|---|---|---|
| `body(channel, position)` | `ChannelBody` | [`GET /v1/body/{channel}`](channel-page.md) |
| `more(channel, direction, position)` | `More` | [`GET /v1/more/{channel}/{direction}/{position}`](more.md) |
| `post(channel, id)` | `Post` | [`GET /v1/post/{channel}/{identifier}`](post.md) |
| `preview(channel)` | `Preview` | [`GET /v1/preview/{channel}`](previews.md) |
| `previews(channels)` | `[Preview!]` | [`POST /v1/previews`](previews.md) |
| `feed(channels)` | `[FeedPost!]` | [`POST /v1/feed`](feed.md) |

**Field names are the REST JSON keys** (`preview_link`, `to_message`,
`is_verified`, …), so a GraphQL result is exactly the REST response with the
fields you did not ask for left out. The [data models](models.md) therefore
describe both APIs. The differences:

- `preview` and `previews` return `Preview` objects with an extra `username`
  field; `previews` is a list (in request order, unknown channels omitted)
  instead of a map keyed by username.
- Feed items are `FeedPost`: a `Post` plus `channel`, `score`, `duplicates`
  and `topic` (REST: `_score`, `_duplicates`, `_topic`), in feed order. Fields
  absent from the raw feed data are `null`.
- Fixed sets of strings are enums whose values are the REST strings:
  `MediaType` (`image`, `video`, …), `EntityType`, `ReactionType` and
  `Direction` (`after`, `before`).
- `Date.unix` is a `Long` scalar: a plain JSON integer that may exceed 32 bits.

Root fields are nullable: when one fails it is `null` with an entry in
`errors`, and the other fields of the operation still return data.

??? abstract "Full schema (SDL)"

    ```graphql
    --8<-- "tests/snapshots/graphql/schema.graphql"
    ```

## Examples

Latest post IDs, views and pagination cursor of a channel:

```graphql
{
  body(channel: "durov") {
    content { posts { id footer { views date { unix } } } }
    meta { offset { before } }
  }
}
```

The next page, with variables:

```graphql
query Older($channel: String!, $before: Int!) {
  more(channel: $channel, direction: before, position: $before) {
    posts { id content { text { string } } }
    meta { offset { before } }
  }
}
```

```json
{"channel": "durov", "before": 531}
```

Several channels in one request, using aliases and a fragment:

```graphql
{
  durov: body(channel: "durov") { ...Latest }
  telegram: body(channel: "telegram") { ...Latest }
  previews(channels: ["durov", "telegram"]) {
    username
    channel { title subscribers is_verified }
  }
}

fragment Latest on ChannelBody {
  channel { title { string } }
  content { posts { id content { media { type url thumb } } } }
}
```

A ranked feed (requires [`ENABLE_FEED`](../configuration.md#enable_feed)):

```graphql
{
  feed(channels: ["durov", "telegram", "stranaua"]) {
    id
    score
    channel { username }
    content { text { string } }
    duplicates { channel id }
    topic { keywords }
  }
}
```

A batch of two operations in one HTTP request:

```json
[
  {"query": "{ post(channel: \"durov\", id: 531) { view } }"},
  {"query": "{ preview(channel: \"telegram\") { channel { title } } }"}
]
```

The response is an array with one result per operation, in order.

## Efficiency

- Each page is fetched at most once per HTTP request: equal root fields
  (aliases, repeated fragments, the operations of a batch) share one result,
  and fields that need the same page at the same time (`body(position: 531)`
  and `post(id: 531)`) share one upstream request.
- Root fields are resolved concurrently; `previews` and `feed` fetch their
  channels in parallel (still bounded by
  [`TELEGRAM_MAX_CONCURRENCY`](../configuration.md#telegram_max_concurrency)).
- Responses come from the same caches as REST (see
  [Freshness](index.md#freshness)), so mixing both APIs does not double the
  load on t.me. `GET` queries from Cloudflare get the same `Cache-Control` as
  REST responses.

## Limits

Every operation is checked before anything is fetched. All limits are
configurable:

| Setting | Default | Rejects |
|---|---|---|
| [`GRAPHQL_MAX_DEPTH`](../configuration.md#graphql_max_depth) | `10` | Selections nested deeper than this below a root field (root field = 0, introspection excepted) |
| [`GRAPHQL_MAX_COMPLEXITY`](../configuration.md#graphql_max_complexity) | `1500` | Operations whose cost exceeds this |
| [`GRAPHQL_MAX_ALIASES`](../configuration.md#graphql_max_aliases) | `15` | Documents with more aliases |
| [`GRAPHQL_MAX_TOKENS`](../configuration.md#graphql_max_tokens) | `2000` | Documents longer than this many tokens |
| [`GRAPHQL_MAX_BATCH_SIZE`](../configuration.md#graphql_max_batch_size) | `10` | Batches with more operations (`400 Too many operations`) |

**Cost** is 1 per selected field (fragments expanded) plus 10 per channel a
root field may fetch: 10 for `body`, `more`, `post` and `preview`, 10 × the
number of channels for `previews` and `feed` (the actual list length, also
when passed as a variable). A `feed` over the maximum 100 channels costs
about 1 000 + its fields, so it fits the default limit; two of them do not.

Limits apply to each operation of a batch. The rate limit
([`ENABLE_RATE_LIMIT`](../configuration.md#enable_rate_limit)) counts HTTP
requests, so a batch counts once.

## Errors

GraphQL errors are returned with HTTP `200` in the `errors` list, each with a
stable `extensions.code`:

```json
{
  "data": {"body": null},
  "errors": [
    {
      "message": "Requested Telegram resource was not found",
      "locations": [{"line": 1, "column": 3}],
      "path": ["body"],
      "extensions": {"code": "NOT_FOUND"}
    }
  ]
}
```

| `extensions.code` | REST status | Cause |
|---|---|---|
| `BAD_INPUT` | `422` | Invalid username, post ID/position out of `1..10000000`, or not 1–100 channels |
| `NOT_FOUND` | `404` | Channel or post does not exist (or is private) |
| `BAD_GATEWAY` | `502` | t.me answered with a page the parser could not interpret |
| `UNAVAILABLE` | `503` | t.me is unreachable or answered with an error |
| `FEATURE_DISABLED` | `503` | `preview`/`previews` with `ENABLE_PREVIEWS=false`, `feed` with `ENABLE_FEED=false` |
| `QUERY_TOO_COMPLEX` | — | The operation costs more than `GRAPHQL_MAX_COMPLEXITY` |
| `GRAPHQL_PARSE_FAILED` | — | Syntax error, or more than `GRAPHQL_MAX_TOKENS` tokens |
| `GRAPHQL_VALIDATION_FAILED` | — | Unknown field or argument, wrong type, depth/alias limit, introspection while disabled |
| `INTERNAL_SERVER_ERROR` | `500` | Unexpected server error; the message is masked and the details are logged |

As in REST, input is validated before feature flags are checked, so invalid
arguments are `BAD_INPUT` even while a feature is disabled. Errors in the
`BAD_INPUT`…`FEATURE_DISABLED` rows are execution errors: they null only their
field. The others are raised before execution and return `"data": null`.

Requests that are not GraphQL at all are rejected with an HTTP status: `400`
for a body that is not JSON, has no `query`, or is a too large batch, `400`
for `GET` queries while docs are disabled, `429` when rate limited.
