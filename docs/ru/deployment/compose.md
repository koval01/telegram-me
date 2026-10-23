# Docker Compose

[`compose.yaml`](https://github.com/koval01/telegram-me/blob/main/compose.yaml)
в корне репозитория запускает полноценный экземпляр на одном хосте:

| Сервис | Образ | Назначение |
|---|---|---|
| `api` | `ghcr.io/koval01/telegram-me` (или локальная сборка) | API на `${APP_PORT:-8000}` |
| `valkey` | `valkey/valkey:9.1-alpine` | Общий кэш (совместим с Redis), только в памяти, вытеснение LRU |
| `worker` | тот же, что `api`, `command: worker` | Необязательный рефрешер ленты (профиль `worker`) |

## Использование { #usage }

```bash
docker compose up -d --wait                       # published image
docker compose up -d --build --wait               # build from the checkout
PRESET=full docker compose up -d --wait           # enable the feed
docker compose --profile worker up -d --wait      # + standalone feed worker
docker compose logs -f api
docker compose down
```

`--wait` возвращает управление, когда все сервисы здоровы.

## Конфигурация { #configuration }

Настройки накладываются слоями, более поздний важнее:

1. `deploy/presets/${PRESET:-standard}.env`
2. `.env` рядом с `compose.yaml` (необязательно)
3. `REDIS_URL` по умолчанию указывает на встроенный Valkey
   (`redis://valkey:6379/0`), если вы не задали его в окружении оболочки.

Полезные переменные для самого compose:

| Переменная | По умолчанию | Значение |
|---|---|---|
| `PRESET` | `standard` | `standard`, `full` или `minimal` |
| `APP_PORT` | `8000` | Порт на хосте |
| `APP_IMAGE` | `ghcr.io/koval01/telegram-me:latest` | Запускаемый образ |
| `VALKEY_MAXMEMORY` | `256mb` | Размер кэша |

## Усиление защиты { #hardening }

Оба сервиса приложения работают с корневой файловой системой только для
чтения, `tmpfs` на `/tmp`, без каких-либо Linux capabilities, с
`no-new-privileges`, ротацией логов (5 × 20 МБ) и `restart: unless-stopped`.
Valkey не хранит данные на диске: после перезапуска кэш заново наполняется с
t.me.
