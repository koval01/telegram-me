# Превью каналов

Превью — это краткие сводки о каналах, взятые с главной страницы канала
`https://t.me/<channel>`: дешевле полной страницы канала и по умолчанию
кэшируются на час
([`PREVIEW_CACHE_TTL`](../configuration.md#preview_cache_ttl)).

Оба эндпоинта отвечают `503`, если превью отключены через
[`ENABLE_PREVIEWS=false`](../configuration.md#enable_previews).

## `GET /v1/preview/{channel}`

```bash
curl http://localhost:8000/v1/preview/durov
```

```json
{
  "channel": {
    "title": "Pavel Durov",
    "subscribers": "10 281 023 subscribers",
    "description": "Founder of Telegram.",
    "avatar": "https://cdn4.telesco.pe/file/TUoXCel1JUtqk….jpg",
    "is_verified": true
  }
}
```

| Поле | Тип | Примечания |
|---|---|---|
| `title` | string | Название канала |
| `subscribers` | string | Так, как показывает Telegram, включая слово «subscribers» |
| `description` | string \| null | Простой текст; от ссылок остаётся их текст, `<br>` превращается в `\n` |
| `avatar` | URL \| null | `null`, если Telegram встраивает изображение прямо в страницу |
| `is_verified` | boolean | Значок верификации |

`404` возвращается для несуществующих каналов, приватных каналов,
пользователей и ботов. Неизвестные каналы запоминаются на 60 секунд, так что
повторные запросы не доходят до t.me.

## `POST /v1/previews`

Превью до 100 каналов за один вызов, загружаемые параллельно.

```bash
curl -X POST http://localhost:8000/v1/previews \
  -H 'content-type: application/json' \
  -d '{"channels": ["durov", "telegram", "nosuchchannel123"]}'
```

```json
{
  "durov":    {"channel": {"title": "Pavel Durov", "subscribers": "10 281 023 subscribers", "description": "Founder of Telegram.", "avatar": "https://…", "is_verified": true}},
  "telegram": {"channel": {"title": "Telegram News", "subscribers": "9 257 928 subscribers", "description": "The official Telegram on Telegram. Much recursion. …", "avatar": "https://…", "is_verified": true}}
}
```

- Ключи — имена пользователей в том виде, в каком они были отправлены;
  дубликаты загружаются один раз.
- Несуществующие каналы в результат **не включаются**.
- Если все каналы не удалось загрузить по другой причине (например, t.me
  недоступен), запрос завершается этой ошибкой (`503`).

!!! note "Изменено в 2.0"
    В 1.x весь запрос получал `404`, если не находился хотя бы один канал.
    2.0 возвращает те каналы, которые удалось загрузить.

Тело запроса: `{"channels": [string, …]}` с 1–100 корректными именами
пользователей; голый JSON-массив отклоняется с `422`.
