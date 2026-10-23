# FAQ

**Does it need a Telegram account or bot token?**
No. It reads the same public web pages a browser shows at `t.me/s/<channel>`.

**Which channels work?**
Public channels with web preview enabled. Private channels, groups, users and
bots are reported as `404`.

**Are all posts available?**
Everything the web preview shows. Telegram omits some content there (for
example very large videos are marked `available: false`, and comments are not
included).

**How fresh is the data?**
At most 10 s old with the default `CACHE_MODE=normal` (120 s with
`aggressive`, live with `off`). Previews are cached for an hour. See
[API overview → Freshness](api/index.md#freshness).

**Will Telegram block my server?**
Heavy uncached traffic can get an IP rate-limited by t.me (`503` responses).
Keep caching on, use Redis when you run several workers, keep
`TELEGRAM_MAX_CONCURRENCY` moderate and put a CDN or proxy cache in front for
public deployments.

**Why is `/v1/feed` disabled?**
It needs a page of every channel in the request, so it is much more expensive
than the other endpoints. Enable it with `ENABLE_FEED=true`, ideally with
Redis.

**Why are counters strings?**
They are returned exactly as Telegram formats them (`10.3M`), which can be
lossy; converting them would suggest precision that does not exist.

**Why is `text.entities[].offset` sometimes surprising?**
Offsets index the plain `string` as Python counts characters (code points),
not UTF-16 code units as the Telegram Bot API does.

**I get `502`.**
Telegram probably changed its markup. Please
[open an issue](https://github.com/koval01/telegram-me/issues) with the URL.

**I get `422` for a valid channel.**
Usernames must be 5–32 characters, start with a letter and contain only
letters, digits and `_`. Pass them without `@`.

**Can I run it without Docker?**
Yes: `uv run telegram-me serve` (or `pip install .` and `telegram-me serve`).
