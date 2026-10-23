# Страница канала

```http
GET /v1/body/{channel}?position={id}
```

Возвращает профиль канала, страницу постов (около 20, от старых к новым) и
курсоры для подгрузки. Соответствует `https://t.me/s/{channel}`, а с
`position` — `https://t.me/s/{channel}/{position}`.

## Параметры { #parameters }

| Имя | Где | Тип | Обязательный | Описание |
|---|---|---|---|---|
| `channel` | path | string | да | Имя пользователя канала, например `durov` |
| `position` | query | integer 1…10 000 000 | нет | Открыть страницу вокруг этого поста вместо последних постов |

## Ответ { #response }

Объект [`ChannelBody`](models.md#channelbody):

```json
{
  "channel": {
    "username": "durov",
    "title": {"string": "Pavel Durov", "html": "Pavel Durov"},
    "description": {"string": "Founder of Telegram.", "html": "Founder of Telegram."},
    "avatar": "https://cdn4.telesco.pe/file/TUoXCel1JUtqk….jpg",
    "counters": {
      "subscribers": "10.3M",
      "photos": "102",
      "videos": "47",
      "files": null,
      "links": "200"
    },
    "labels": ["verified"]
  },
  "content": {
    "posts": [
      {
        "id": 552,
        "content": {
          "text": {
            "string": "✨ Designers × AI\n\n🆕 The mockup era is over …",
            "html": "<tg-emoji emoji-id=\"5325547803936572038\"><i class=\"emoji\" …><b>✨</b></i></tg-emoji> <b>Designers × AI<br/><br/></b> …",
            "entities": [
              {"offset": 0, "length": 1, "type": "animoji", "url": "https://telegram.org/img/emoji/40/E29CA8.png", "language": null},
              {"offset": 2, "length": 16, "type": "bold", "url": null, "language": null}
            ]
          },
          "media": [
            {
              "url": null,
              "thumb": "https://cdn4.telesco.pe/file/Sugu_UFeS….jpg",
              "waves": null,
              "duration": {"formatted": "1:15", "raw": 75},
              "type": "video",
              "available": false
            }
          ],
          "poll": null,
          "inline": null,
          "reply": null,
          "preview_link": null,
          "reacts": [
            {"count": "6.07K", "type": "telegram_stars", "emoji": "⭐", "emoji_id": null, "emoji_image": null},
            {"count": "14.2K", "type": "custom_emoji", "emoji": null, "emoji_id": "5936157098181135162", "emoji_image": null}
          ]
        },
        "footer": {
          "views": "1.34M",
          "edited": false,
          "author": {"string": "Pavel Durov", "html": "Pavel Durov"},
          "date": {"string": "2026-10-11T14:52:40+00:00", "unix": 1791730360}
        },
        "forwarded": null,
        "view": "eyJjIjotMTAwNjUwMzEyMiwicCI6NTUyLCJ0IjoxNzky…"
      }
    ]
  },
  "meta": {"offset": {"before": 531, "after": null}}
}
```

### На что обратить внимание { #fields-worth-knowing }

- `channel.counters` содержит только то, что показывает Telegram; неизвестные
  счётчики равны `null`.
- `channel.labels` перечисляет значки в шапке, например `verified`; `null`,
  если их нет.
- `content.posts[].view` — непрозрачный токен просмотра поста от Telegram.
- `meta.offset.before` / `after` — ID постов, которые передаются в
  [`/v1/more`](more.md). На самой новой странице `after` равен `null`.

Каждый пост подробно описан в разделе [Модели → Post](models.md#post).

## Ошибки { #errors }

| Статус | Когда |
|---|---|
| `404` | Канал не существует, приватный, или это пользователь/бот, а не канал |
| `422` | Некорректный `channel` или `position` |
| `502` | Не удалось разобрать страницу |
| `503` | t.me недоступен или ответил ошибкой |

Отдельный пост с неожиданной разметкой пропускается (и записывается в лог), а
не ломает всю страницу.

## Примеры { #examples }

=== "curl"

    ```bash
    curl "http://localhost:8000/v1/body/durov"
    curl "http://localhost:8000/v1/body/stranaua?position=252483"
    ```

=== "Python"

    ```python
    import httpx

    page = httpx.get("http://localhost:8000/v1/body/durov").json()
    for post in page["content"]["posts"]:
        text = (post["content"].get("text") or {}).get("string", "")
        print(post["id"], post["footer"]["views"], text[:60])
    ```

=== "JavaScript"

    ```javascript
    const page = await fetch("http://localhost:8000/v1/body/durov").then((r) => r.json());
    console.log(page.channel.title.string, page.content.posts.length);
    ```
