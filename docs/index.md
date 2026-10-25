---
title: TelegramMe
description: REST API for public Telegram channels, built on t.me web previews.
---

# TelegramMe

TelegramMe turns the public web preview of any Telegram channel
(`https://t.me/s/<channel>`) into a clean, typed JSON API: channel profiles,
posts with text entities, media, polls, reactions, replies, forwards and
inline buttons, cursor pagination, channel previews and a ranked multi-channel
feed.

No Telegram account, bot token or MTProto session is needed — only public
channels are reachable, exactly as a browser sees them.

<div class="grid cards" markdown>

-   :lucide-rocket: **Run it in a minute**

    ---

    One `docker run` or `docker compose up` and the API is on port 8000.

    [:octicons-arrow-right-24: Quickstart](getting-started/quickstart.md)

-   :lucide-book-open: **API reference**

    ---

    Every endpoint, parameter, response field and error, with real examples.

    [:octicons-arrow-right-24: API reference](api/index.md)

-   :lucide-server: **Production deployment**

    ---

    Hardened containers, multi-server rollouts with automatic rollback,
    reverse proxy and caching setup.

    [:octicons-arrow-right-24: Deployment](deployment/production.md)

-   :lucide-cpu: **How it works**

    ---

    Layers, caching, request coalescing, the feed refresher and the native
    (Rust / C) components that do the heavy lifting.

    [:octicons-arrow-right-24: Internals](internals/architecture.md)

</div>

## Try it

A public demo runs at **<https://tme.koval-dev.org>**: try a channel on its
landing page, explore the API in the [Swagger UI](https://tme.koval-dev.org/api)
or read these docs at [/docs](https://tme.koval-dev.org/docs/).

```bash
curl https://tme.koval-dev.org/v1/body/durov
```

```json
{
  "channel": {
    "username": "durov",
    "title": {"string": "Pavel Durov", "html": "Pavel Durov"},
    "description": {"string": "Founder of Telegram.", "html": "Founder of Telegram."},
    "avatar": "https://cdn4.telesco.pe/file/…",
    "counters": {"subscribers": "10.3M", "photos": "102", "videos": "47", "files": null, "links": "200"},
    "labels": ["verified"]
  },
  "content": {"posts": [ … 20 posts … ]},
  "meta": {"offset": {"before": 531, "after": null}}
}
```

## Endpoints at a glance

| Method | Path | What it returns |
|---|---|---|
| `GET` | [`/v1/body/{channel}`](api/channel-page.md) | Channel profile, latest posts (or posts around `?position=`), pagination cursors |
| `GET` | [`/v1/more/{channel}/{direction}/{position}`](api/more.md) | Older (`before`) or newer (`after`) posts relative to a post |
| `GET` | [`/v1/post/{channel}/{id}`](api/post.md) | One post |
| `GET` | [`/v1/preview/{channel}`](api/previews.md#get-v1previewchannel) | Short channel summary from `t.me/<channel>` |
| `POST` | [`/v1/previews`](api/previews.md#post-v1previews) | Up to 100 previews at once |
| `POST` | [`/v1/feed`](api/feed.md) | Posts of up to 100 channels, ranked by engagement and freshness (off by default) |
| `GET` | [`/healthz`](api/service.md) | Liveness probe |

## What is inside

- **FastAPI + Pydantic v2** for the HTTP layer and strict response models.
- **wreq** (Rust: tokio, BoringSSL) for all traffic to t.me — one pooled
  HTTP/2 client outside the GIL whose TLS, HTTP/2 and headers match one
  browser ([HTTP client](internals/http-client.md)); pyreqwest as a fallback.
- **telegram-me-native** (Rust, this project) for HTML parsing (html5ever) and feed ranking.
- **granian** (Rust) as the HTTP server, with **uvloop** (C) as event loop.
- An in-process cache in Rust (**moka**, zstd) per worker, in front of
  **Redis / Valkey** (optional, with **hiredis**, C) shared by all workers and
  servers.

See [Performance](internals/performance.md) for measured numbers.

## License and data

telegram-me is licensed under
[Apache-2.0](https://github.com/koval01/telegram-me/blob/main/LICENSE). It is an
independent project, not affiliated with Telegram. It reads public t.me pages:
the posts, media and channel details it returns belong to their authors and
are not covered by the license. If you run an instance, you are responsible
for following Telegram's terms of service and the laws that apply to you,
including data protection law.
