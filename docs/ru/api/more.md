# Пагинация

```http
GET /v1/more/{channel}/{direction}/{position}
```

Загружает посты старше (`before`) или новее (`after`) поста `position` — как
при прокрутке канала на t.me. Сам опорный пост не включается.

## Параметры { #parameters }

| Имя | Где | Тип | Описание |
|---|---|---|---|
| `channel` | path | string | Имя пользователя канала |
| `direction` | path | `before` \| `after` | `before` — более старые посты, `after` — более новые |
| `position` | path | integer 1…10 000 000 | ID опорного поста |

## Ответ { #response }

Объект [`More`](models.md#more): `posts` (той же формы, что и на странице
канала) и `meta.offset` со следующими курсорами. Например,
`GET /v1/more/durov/before/531` возвращает посты 510…529:

```json
{
  "posts": [ { "id": 510, "content": { … }, "footer": { … }, "forwarded": null, "view": "…" }, … ],
  "meta": {"offset": {"before": 510, "after": null}}
}
```

## Обход всего канала { #walking-a-whole-channel }

```python
import httpx

api = "http://localhost:8000/v1"
page = httpx.get(f"{api}/body/durov").json()
posts = page["content"]["posts"]
cursor = page["meta"]["offset"]["before"]

while cursor:
    batch = httpx.get(f"{api}/more/durov/before/{cursor}").json()
    if not batch["posts"]:
        break
    posts = batch["posts"] + posts
    cursor = batch["meta"]["offset"]["before"]

print(len(posts), "posts")
```

!!! tip "Бережнее"
    Каждая некэшированная страница — это запрос к t.me. Быстрый обход больших
    каналов может привести к тому, что Telegram ограничит частоту запросов с
    IP сервера; добавьте паузу между страницами.

## Ошибки { #errors }

`404` для неизвестных каналов, `422` для некорректных параметров, `502`/`503`
— как описано в разделе [Ошибки](errors.md).
