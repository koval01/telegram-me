# Отдельный пост

```http
GET /v1/post/{channel}/{identifier}
```

Возвращает один [`Post`](models.md#post). Внутри читается та же страница, что
и для `/v1/body/{channel}?position={identifier}`, поэтому запрос поста, а затем
окружающей его страницы стоит одного обращения к t.me.

## Параметры { #parameters }

| Имя | Где | Тип | Описание |
|---|---|---|---|
| `channel` | path | string | Имя пользователя канала |
| `identifier` | path | integer 1…10 000 000 | ID поста (число в `t.me/<channel>/<id>`) |

## Пример { #example }

`GET /v1/post/nebudetgg/6209` — пост с кнопкой inline-клавиатуры:

```json
{
  "id": 6209,
  "content": {
    "text": {"string": "…", "html": "…", "entities": [ … ]},
    "media": [ … ],
    "inline": [
      {
        "title": "Участвовать (3145)",
        "url": "https://t.me/Random/JoinLot?startapp=2154773G7806d14&startApp=2154773G7806d14"
      }
    ],
    "poll": null,
    "reply": null,
    "preview_link": null,
    "reacts": [ … ]
  },
  "footer": {"views": "207K", "edited": false, "author": null, "date": {"string": "2026-05-12T09:39:25+00:00", "unix": 1778578765}},
  "forwarded": {
    "name": {"string": "", "html": ""},
    "url": null,
    "author": {"string": "RandomGodBot⚡️ [Рандомайзер]", "html": "RandomGodBot<i class=\"emoji\" …><b>⚡️</b></i> [Рандомайзер]"}
  },
  "view": "…"
}
```

## Ошибки { #errors }

| Статус | Когда |
|---|---|
| `404` | Канал не существует, или поста нет на странице (удалён или никогда не существовал) |
| `422` | Некорректные параметры |
| `502` | Пост существует, но его не удалось разобрать |
| `503` | t.me недоступен |
