# No public instance

TelegramMe has no public instance, and there will not be one. Run it on your
own server or on your own machine: it is one container.

The demo that ran at `tme.koval-dev.org` was switched off for good in
November 2026, together with its servers and its deployment pipeline. Its
address no longer serves the API, and nothing replaces it.

## Why

The demo was there to show what the project does, not to serve other
people's applications. It was used for exactly that:

- **Applications were built on it.** Instead of running their own instance,
  or building TelegramMe into their product, people pointed their apps at the
  demo and treated it as a free API. The project is open source so that
  anyone can run it, not so that one person runs it for everyone.
- **Someone else's traffic, the maintainer's responsibility.** Every request
  went to t.me from the demo's servers. With no way to know who sends it and
  why, the demo could, sooner or later, end up serving abuse or crime. The
  maintainer does not want to carry that.
- **The protection was there only for the abuse.** Rate limits, bot
  protection and edge rules kept the demo alive, and still did not stop it
  from being used as a backend.

## What to do instead

**Run your own instance.** The image is public and needs no account, key or
token:

```bash
docker run -d --name telegram-me -p 8000:3000 --restart unless-stopped \
  ghcr.io/koval01/telegram-me:latest
curl http://localhost:8000/v1/body/durov
```

It serves the same API the demo served, with the landing page, the Swagger UI
at `/api` and these docs at `/docs/`. The [Quickstart](getting-started/quickstart.md)
covers this and Docker Compose with Valkey.

**Build it into your application.** Run TelegramMe next to your app, in the
same compose project or pod, and let only your app reach it: no published
port, no public address. Your app calls it over the private network like any
other internal service. See [Docker Compose](deployment/compose.md).

**Scale out on your own servers.** Several servers share one Redis; see
[Docker Compose](deployment/compose.md), [Peers](deployment/peers.md) and
[Reverse proxy](deployment/reverse-proxy.md).

**Expose it to the internet only on purpose.** If your instance is public,
turn on the [rate limit and proof of work](api/protection.md) and keep caching
on. Whoever runs an instance is responsible for its traffic and for
following Telegram's terms and the law (see `NOTICE`).

**Moving off the demo.** Change the base URL to your instance. The paths,
parameters and responses are the same, because it is the same image.
