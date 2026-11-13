# TelegramMe

[![CI](https://github.com/koval01/telegram-me/actions/workflows/ci.yml/badge.svg)](https://github.com/koval01/telegram-me/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://koval01.github.io/telegram-me/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15912/badge)](https://www.bestpractices.dev/projects/15912)

REST API for public Telegram channels. It turns `t.me/s/<channel>` into typed
JSON — channel profiles, posts with text entities, media, polls, reactions,
replies, forwards and buttons, pagination, previews and a ranked multi-channel
feed. No Telegram account or bot token needed.

**Docs:** [English](https://koval01.github.io/telegram-me/), [Español](https://koval01.github.io/telegram-me/es/), [Русский](https://koval01.github.io/telegram-me/ru/) ·
**API reference:** [REST](https://koval01.github.io/telegram-me/api/), [GraphQL](https://koval01.github.io/telegram-me/api/graphql/)

Every instance also serves its own copy of the docs at `/docs`, the Swagger UI
at `/api` and GraphQL at `/graphql`.

## No public instance

There is no public demo and there will not be one: run TelegramMe on your own
server or locally. The demo that ran at `tme.koval-dev.org` was switched off
for good, because applications used it as a free backend instead of running
their own instance, and a shared instance that sends anyone's traffic to t.me
can end up serving abuse. Self-hosting takes one command (below), and the
image serves the same API the demo did; to move off the demo, change the base
URL. Details: [No public instance](https://koval01.github.io/telegram-me/no-public-instance/).

## Run on your server

```bash
docker run -d --name telegram-me -p 8000:3000 --restart unless-stopped \
  ghcr.io/koval01/telegram-me:latest
```

With a shared cache and presets (recommended):

```bash
git clone https://github.com/koval01/telegram-me && cd telegram-me
docker compose up -d --wait            # API on :8000 + Valkey
```

Then try it: `curl http://localhost:8000/v1/body/durov`. To build it into
your app, run it next to the app on a private network, without a public port.

Configuration, multi-server deployment and reverse proxy setup:
[deployment guide](https://koval01.github.io/telegram-me/deployment/compose/).

## Run locally for development

```bash
git clone https://github.com/koval01/telegram-me && cd telegram-me
uv sync
uv run telegram-me serve --reload      # http://localhost:3000
make check                             # lint, types, tests
```

Requires [uv](https://docs.astral.sh/uv/). See the
[development guide](https://koval01.github.io/telegram-me/getting-started/local-development/).

## License

[Apache-2.0](LICENSE). telegram-me is not affiliated with Telegram. It reads
public t.me pages; the content it returns belongs to its authors, and whoever
runs it is responsible for following Telegram's terms and applicable law. See
[NOTICE](NOTICE).
