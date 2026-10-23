# Errors

Every error except `422` and `429` has the same body (the GraphQL API reports
errors differently, see [GraphQL errors](graphql.md#errors)):

```json
{"detail": "Requested Telegram resource was not found"}
```

## Reference

| Status | `detail` | Cause |
|---|---|---|
| `404` | `Requested Telegram resource was not found` | Channel does not exist, is private, or is not a channel |
| `404` | `Requested post was not found` | `/v1/post`: the post is not on its page |
| `422` | *list, see below* | Request validation failed |
| `422` | `Invalid Telegram username format` / `Invalid post position` | Input rejected by the Telegram client (rare: the API validates first) |
| `429` | `Rate limit exceeded` *(plain text)* | More than `RATE_LIMIT_RPM` requests from one IP within a minute |
| `502` | `Failed to parse Telegram channel page` (and similar) | t.me answered, but not with the expected markup |
| `502` | `Telegram response did not match the expected schema` | Parsed data could not be validated |
| `503` | `Failed to fetch Telegram upstream resource` | Network error or timeout talking to t.me |
| `503` | `Telegram upstream returned status 429` (any ≥ 400) | t.me rejected the request |
| `503` | `Feature 'feed' is disabled by server configuration` | Feature switched off (`feed`, `previews`) |

## 422 validation errors

Validation errors use FastAPI's standard format:

```json
{
  "detail": [
    {
      "type": "string_pattern_mismatch",
      "loc": ["path", "channel"],
      "msg": "String should match pattern '^[a-zA-Z][a-zA-Z0-9_]{3,31}$'",
      "input": "bad-name",
      "ctx": {"pattern": "^[a-zA-Z][a-zA-Z0-9_]{3,31}$"}
    }
  ]
}
```

## Retrying

- `503` from t.me problems is usually transient: retry with backoff.
- `502` usually means Telegram changed its markup; retrying will not help —
  please [open an issue](https://github.com/koval01/telegram-me/issues) with
  the channel and URL.
- `429`: wait for `Retry-After` seconds.

!!! note "Changed in 2.0"
    Responses that could not be validated against the schema were `400` in
    1.x; they are `502` now. A single unexpected post no longer fails a page —
    it is skipped and logged.
