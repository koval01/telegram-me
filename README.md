# TelegramMe

[![CI](https://github.com/koval01/telegram-me/actions/workflows/ci.yml/badge.svg)](https://github.com/koval01/telegram-me/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-tme.koval--dev.org-blue)](https://tme.koval-dev.org/docs/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15912/badge)](https://www.bestpractices.dev/projects/15912)

REST API for public Telegram channels. It turns `t.me/s/<channel>` into typed
JSON — channel profiles, posts with text entities, media, polls, reactions,
replies, forwards and buttons, pagination, previews and a ranked multi-channel
feed. No Telegram account or bot token needed.

**Demo:** <https://tme.koval-dev.org> ·
**API reference:** [REST](https://tme.koval-dev.org/api), [GraphQL](https://tme.koval-dev.org/graphql) ·
**Docs:** [English](https://tme.koval-dev.org/docs/),
[Español](https://tme.koval-dev.org/docs/es/),
[Русский](https://tme.koval-dev.org/docs/ru/)

Every instance serves its own copy of the docs at `/docs`; they are also
published on [GitHub Pages](https://koval01.github.io/telegram-me/).

```bash
curl https://tme.koval-dev.org/v1/body/durov
```

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

Configuration, multi-server deployment and reverse proxy setup:
[deployment guide](https://tme.koval-dev.org/docs/deployment/production/).

## Run locally for development

```bash
git clone https://github.com/koval01/telegram-me && cd telegram-me
uv sync
uv run telegram-me serve --reload      # http://localhost:3000
make check                             # lint, types, tests
```

Requires [uv](https://docs.astral.sh/uv/). See the
[development guide](https://tme.koval-dev.org/docs/getting-started/local-development/).

## License

[Apache-2.0](LICENSE). telegram-me is not affiliated with Telegram. It reads
public t.me pages; the content it returns belongs to its authors, and whoever
runs it is responsible for following Telegram's terms and applicable law. See
[NOTICE](NOTICE).
