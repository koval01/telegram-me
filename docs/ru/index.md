---
title: TelegramMe
description: REST API для публичных Telegram-каналов на основе веб-превью t.me.
---

# TelegramMe

TelegramMe превращает публичное веб-превью любого Telegram-канала
(`https://t.me/s/<channel>`) в аккуратный типизированный JSON API: профили
каналов, посты с текстовыми сущностями, медиа, опросы, реакции, ответы,
пересылки и inline-кнопки, курсорная пагинация, превью каналов и ранжированная
лента из нескольких каналов.

Не нужны ни аккаунт Telegram, ни токен бота, ни сессия MTProto: доступны только
публичные каналы, ровно так, как их видит браузер.

<div class="grid cards" markdown>

-   :lucide-rocket: **Запуск за минуту**

    ---

    Один `docker run` или `docker compose up` — и API работает на порту 8000.

    [:octicons-arrow-right-24: Быстрый старт](getting-started/quickstart.md)

-   :lucide-book-open: **Справочник API**

    ---

    Каждый эндпоинт, параметр, поле ответа и ошибка — с реальными примерами.

    [:octicons-arrow-right-24: Справочник API](api/index.md)

-   :lucide-server: **Развёртывание в продакшене**

    ---

    Защищённые контейнеры, развёртывание на нескольких серверах с
    автоматическим откатом, обратный прокси и настройка кэширования.

    [:octicons-arrow-right-24: Развёртывание](deployment/production.md)

-   :lucide-cpu: **Как это работает**

    ---

    Слои, кэширование, объединение запросов, фоновое обновление ленты и
    нативные компоненты (Rust / C), которые берут на себя тяжёлую работу.

    [:octicons-arrow-right-24: Внутреннее устройство](internals/architecture.md)

</div>

## Попробуйте { #try-it }

Публичное демо работает по адресу **<https://tme.koval-dev.org>**
(интерактивный Swagger UI в корне):

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

## Эндпоинты в двух словах { #endpoints-at-a-glance }

| Метод | Путь | Что возвращает |
|---|---|---|
| `GET` | [`/v1/body/{channel}`](api/channel-page.md) | Профиль канала, последние посты (или посты вокруг `?position=`), курсоры пагинации |
| `GET` | [`/v1/more/{channel}/{direction}/{position}`](api/more.md) | Более старые (`before`) или более новые (`after`) посты относительно заданного |
| `GET` | [`/v1/post/{channel}/{id}`](api/post.md) | Один пост |
| `GET` | [`/v1/preview/{channel}`](api/previews.md#get-v1previewchannel) | Краткая сводка о канале со страницы `t.me/<channel>` |
| `POST` | [`/v1/previews`](api/previews.md#post-v1previews) | До 100 превью за раз |
| `POST` | [`/v1/feed`](api/feed.md) | Посты до 100 каналов, ранжированные по вовлечённости и свежести (по умолчанию выключено) |
| `GET` | [`/healthz`](api/service.md) | Проверка живости (liveness probe) |

## Что внутри { #what-is-inside }

- **FastAPI + Pydantic v2** — HTTP-слой и строгие модели ответов.
- **pyreqwest** (Rust: tokio, hyper, rustls) — весь трафик к t.me: один HTTP/2
  клиент с пулом соединений, работающий вне GIL.
- **telegram-me-native** (Rust, часть этого проекта) — разбор HTML (html5ever)
  и ранжирование ленты.
- **granian** (Rust) — HTTP-сервер, **uvloop** (C) — цикл событий.
- **Redis / Valkey** (необязательно) с **hiredis** (C) — кэш, общий для всех
  воркеров и серверов; без него используется кэш внутри процесса.

Измеренные цифры — на странице [Производительность](internals/performance.md).

## Лицензия и данные { #license-and-data }

telegram-me распространяется по лицензии
[Apache-2.0](https://github.com/koval01/telegram-me/blob/main/LICENSE). Это
независимый проект, не связанный с Telegram. Он читает публичные страницы
t.me: посты, медиа и сведения о каналах, которые он возвращает, принадлежат их
авторам и не подпадают под лицензию. Если вы запускаете свой экземпляр, вы
отвечаете за соблюдение условий использования Telegram и применимых к вам
законов, включая законодательство о защите данных.
