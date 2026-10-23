# TelegramMe

[![CI](https://github.com/koval01/telegram-me/actions/workflows/ci.yml/badge.svg)](https://github.com/koval01/telegram-me/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-koval01.github.io-blue)](https://koval01.github.io/telegram-me/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

REST API for public Telegram channels. It turns `t.me/s/<channel>` into typed
JSON — channel profiles, posts with text entities, media, polls, reactions,
replies, forwards and buttons, pagination, previews and a ranked multi-channel
feed. No Telegram account or bot token needed.

**Demo:** <https://telegram-me-gliv.onrender.com> ·
**Docs:** <https://koval01.github.io/telegram-me/>

```bash
curl https://telegram-me-gliv.onrender.com/v1/body/durov
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
[deployment guide](https://koval01.github.io/telegram-me/deployment/production/).

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

[GPL-3.0](LICENSE)
