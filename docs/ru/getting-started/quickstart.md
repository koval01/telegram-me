# Быстрый старт

Три способа запустить экземпляр — от самого быстрого до самого полного. Во всех
случаях API доступен по адресу **<http://localhost:8000>**, а в корне открыт
интерактивный Swagger UI.

## 1. Один контейнер { #1-a-single-container }

```bash
docker run -d --name telegram-me -p 8000:3000 --restart unless-stopped \
  ghcr.io/koval01/telegram-me:latest
```

Образ также публикуется в Docker Hub как `koval01/telegram-me`. Без
дополнительной настройки он использует кэш внутри процесса — идеальный вариант
для одного небольшого экземпляра.

Проверьте:

```bash
curl http://localhost:8000/healthz          # {"status":"ok"}
curl http://localhost:8000/v1/body/durov    # channel page as JSON
```

## 2. Docker Compose с общим кэшем (рекомендуется) { #2-docker-compose-with-a-shared-cache-recommended }

В репозитории есть `compose.yaml`, который запускает API рядом с кэшем
[Valkey](https://valkey.io/) (совместим с Redis):

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
docker compose up -d --wait
```

Выберите пресет через `PRESET`:

| Пресет | Лента | Превью | Лимит запросов | Кэш | Парсер |
|---|---|---|---|---|---|
| `standard` *(по умолчанию)* | выкл. | вкл. | выкл. | `normal` | `full` |
| `full` | вкл. | вкл. | выкл. | `normal` | `full` |
| `minimal` | выкл. | выкл. | 90 запр./мин на IP | `aggressive` | `simplified` |

```bash
PRESET=full docker compose up -d --wait
```

Всё остальное задаётся в файле `.env` рядом с `compose.yaml` — скопируйте
[`.env.example`](https://github.com/koval01/telegram-me/blob/main/.env.example)
и посмотрите все параметры в разделе [Конфигурация](../configuration.md).

## 3. Из исходников { #3-from-source }

Нужен [uv](https://docs.astral.sh/uv/) (он сам установит подходящий Python):

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
uv sync
uv run telegram-me serve --reload   # http://localhost:3000
```

Полный рабочий процесс описан в разделе
[Локальная разработка](local-development.md).

## Что дальше { #next-steps }

- Изучите [справочник API](../api/index.md).
- Поставьте сервис за [обратный прокси](../deployment/reverse-proxy.md).
- Запустите несколько серверов с
  [поэтапным развёртыванием](../deployment/production.md).
