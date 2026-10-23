# Ошибки

У всех ошибок, кроме `422` и `429`, одинаковое тело (GraphQL API сообщает об
ошибках иначе, см. [Ошибки GraphQL](graphql.md#errors)):

```json
{"detail": "Requested Telegram resource was not found"}
```

## Справочник { #reference }

| Статус | `detail` | Причина |
|---|---|---|
| `404` | `Requested Telegram resource was not found` | Канал не существует, приватный или не является каналом |
| `404` | `Requested post was not found` | `/v1/post`: поста нет на его странице |
| `422` | *список, см. ниже* | Запрос не прошёл валидацию |
| `422` | `Invalid Telegram username format` / `Invalid post position` | Ввод отклонён клиентом Telegram (редко: API проверяет раньше) |
| `429` | `Rate limit exceeded` *(простой текст)* | Больше `RATE_LIMIT_RPM` запросов с одного IP за минуту |
| `502` | `Failed to parse Telegram channel page` (и похожие) | t.me ответил, но не той разметкой, которая ожидалась |
| `502` | `Telegram response did not match the expected schema` | Разобранные данные не прошли валидацию |
| `503` | `Failed to fetch Telegram upstream resource` | Сетевая ошибка или тайм-аут при обращении к t.me |
| `503` | `Telegram upstream returned status 429` (любой ≥ 400) | t.me отклонил запрос |
| `503` | `Feature 'feed' is disabled by server configuration` | Функция отключена (`feed`, `previews`) |

## Ошибки валидации 422 { #422-validation-errors }

Ошибки валидации используют стандартный формат FastAPI:

```json
{
  "detail": [
    {
      "type": "string_pattern_mismatch",
      "loc": ["path", "channel"],
      "msg": "String should match pattern '^[a-zA-Z][a-zA-Z0-9_]{3,31}$'",
      "input": "bad-name",
      "ctx": {"pattern": "^[a-zA-Z][a-zA-Z0-9_]{3,31}$"}
    }
  ]
}
```

## Повторные попытки { #retrying }

- `503` из-за проблем t.me обычно временный: повторяйте с увеличивающейся
  паузой.
- `502` обычно означает, что Telegram изменил разметку; повтор не поможет —
  пожалуйста, [создайте issue](https://github.com/koval01/telegram-me/issues)
  с каналом и URL.
- `429`: подождите столько секунд, сколько указано в `Retry-After`.

!!! note "Изменено в 2.0"
    Ответы, не прошедшие валидацию по схеме, в 1.x возвращали `400`; теперь
    это `502`. Один неожиданный пост больше не ломает страницу — он
    пропускается и записывается в лог.
