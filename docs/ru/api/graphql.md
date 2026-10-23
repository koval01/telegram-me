# GraphQL

```http
POST /graphql
```

GraphQL API рядом с REST API `/v1` — для клиентов, которые хотят сами выбирать
получаемые поля или объединять несколько запросов в один обмен с сервером. Он
работает на тех же сервисах, что и REST: то же кэширование, объединение
запросов, валидация и feature flags, те же данные.

Он включён по умолчанию; отключить его можно через
[`ENABLE_GRAPHQL=false`](../configuration.md#enable_graphql), после чего
`/graphql` отвечает `404`.

## Эндпоинт { #endpoint }

| Метод | Что |
|---|---|
| `POST /graphql` | `{"query": …, "variables": {…}, "operationName": …}` в виде JSON или JSON-**массив** таких объектов (пакет, см. [Лимиты](#limits)) |
| `GET /graphql` | В браузере — IDE GraphiQL. С `?query=…&variables=…` — выполняет запрос |

GraphiQL, `GET`-запросы и интроспекция схемы доступны, пока включена
документация; [`DISABLE_DOCS=true`](../configuration.md#disable_docs)
отключает все три (`POST` продолжает работать).

```bash
curl http://localhost:8000/graphql \
  -H 'content-type: application/json' \
  -d '{"query": "{ body(channel: \"durov\") { channel { title { string } } } }"}'
```

```json
{"data": {"body": {"channel": {"title": {"string": "Pavel Durov"}}}}}
```

## Схема { #schema }

Каждое корневое поле повторяет REST-эндпоинт и возвращает те же данные:

| Поле | Возвращает | Аналог в REST |
|---|---|---|
| `body(channel, position)` | `ChannelBody` | [`GET /v1/body/{channel}`](channel-page.md) |
| `more(channel, direction, position)` | `More` | [`GET /v1/more/{channel}/{direction}/{position}`](more.md) |
| `post(channel, id)` | `Post` | [`GET /v1/post/{channel}/{identifier}`](post.md) |
| `preview(channel)` | `Preview` | [`GET /v1/preview/{channel}`](previews.md) |
| `previews(channels)` | `[Preview!]` | [`POST /v1/previews`](previews.md) |
| `feed(channels)` | `[FeedPost!]` | [`POST /v1/feed`](feed.md) |

**Имена полей совпадают с ключами JSON в REST** (`preview_link`,
`to_message`, `is_verified`, …), так что результат GraphQL — это в точности
REST-ответ без полей, которые вы не запросили. Поэтому
[модели данных](models.md) описывают оба API. Отличия:

- `preview` и `previews` возвращают объекты `Preview` с дополнительным полем
  `username`; `previews` — это список (в порядке запроса, без неизвестных
  каналов), а не словарь с именами пользователей в качестве ключей.
- Элементы ленты — `FeedPost`: `Post` плюс `channel`, `score`, `duplicates` и
  `topic` (в REST: `_score`, `_duplicates`, `_topic`). `duplicates` — пустой
  список, если в пост ничего не свёрнуто. Поля, которых нет в сырых данных
  ленты, равны `null`.
- Фиксированные наборы строк — это enum, значения которых совпадают со
  строками REST: `MediaType` (`image`, `video`, …), `EntityType`,
  `ReactionType` и `Direction` (`after`, `before`).
- `Date.unix` — скаляр `Long`: обычное целое число JSON, которое может
  выходить за пределы 32 бит.

Корневые поля допускают `null`: если одно из них завершается ошибкой, оно
равно `null` и получает запись в `errors`, а остальные поля операции всё равно
возвращают данные.

??? abstract "Полная схема (SDL)"

    ```graphql
    --8<-- "tests/snapshots/graphql/schema.graphql"
    ```

## Примеры { #examples }

ID последних постов канала, их просмотры и курсор пагинации:

```graphql
{
  body(channel: "durov") {
    content { posts { id footer { views date { unix } } } }
    meta { offset { before } }
  }
}
```

Следующая страница, с переменными:

```graphql
query Older($channel: String!, $before: Int!) {
  more(channel: $channel, direction: before, position: $before) {
    posts { id content { text { string } } }
    meta { offset { before } }
  }
}
```

```json
{"channel": "durov", "before": 531}
```

Несколько каналов в одном запросе с алиасами и фрагментом:

```graphql
{
  durov: body(channel: "durov") { ...Latest }
  telegram: body(channel: "telegram") { ...Latest }
  previews(channels: ["durov", "telegram"]) {
    username
    channel { title subscribers is_verified }
  }
}

fragment Latest on ChannelBody {
  channel { title { string } }
  content { posts { id content { media { type url thumb } } } }
}
```

Ранжированная лента (требует
[`ENABLE_FEED`](../configuration.md#enable_feed)):

```graphql
{
  feed(channels: ["durov", "telegram", "stranaua"]) {
    id
    score
    channel { username }
    content { text { string } }
  }
}
```

Пакет из двух операций в одном HTTP-запросе:

```json
[
  {"query": "{ post(channel: \"durov\", id: 531) { view } }"},
  {"query": "{ preview(channel: \"telegram\") { channel { title } } }"}
]
```

Ответ — массив с одним результатом на операцию, в том же порядке.

## Эффективность { #efficiency }

- Каждая страница загружается не больше одного раза за HTTP-запрос: одинаковые
  корневые поля (алиасы, повторяющиеся фрагменты, операции пакета) разделяют
  один результат, а поля, которым одновременно нужна одна и та же страница
  (`body(position: 531)` и `post(id: 531)`), разделяют один запрос к
  источнику.
- Корневые поля вычисляются конкурентно; `previews` и `feed` загружают свои
  каналы параллельно (по-прежнему в пределах
  [`TELEGRAM_MAX_CONCURRENCY`](../configuration.md#telegram_max_concurrency)).
- Ответы берутся из тех же кэшей, что и в REST (см.
  [Свежесть данных](index.md#freshness)), так что совместное использование
  обоих API не удваивает нагрузку на t.me. `GET`-запросы, пришедшие через
  Cloudflare, получают тот же `Cache-Control`, что и ответы REST.

## Лимиты { #limits }

Каждая операция проверяется до того, как что-либо загружается. Все лимиты
настраиваются:

| Настройка | По умолчанию | Отклоняет |
|---|---|---|
| [`GRAPHQL_MAX_DEPTH`](../configuration.md#graphql_max_depth) | `10` | Выборки, вложенные глубже этого значения ниже корневого поля (корневое поле = 0, интроспекция не считается) |
| [`GRAPHQL_MAX_COMPLEXITY`](../configuration.md#graphql_max_complexity) | `1500` | Операции, стоимость которых превышает это значение |
| [`GRAPHQL_MAX_ALIASES`](../configuration.md#graphql_max_aliases) | `15` | Документы с большим числом алиасов |
| [`GRAPHQL_MAX_TOKENS`](../configuration.md#graphql_max_tokens) | `2000` | Документы длиннее этого числа токенов |
| [`GRAPHQL_MAX_BATCH_SIZE`](../configuration.md#graphql_max_batch_size) | `10` | Пакеты с большим числом операций (`400 Too many operations`) |

**Стоимость** — 1 за каждое выбранное поле (с раскрытыми фрагментами) плюс 10
за каждый канал, который может загрузить корневое поле: 10 для `body`, `more`,
`post` и `preview` и 10 × число каналов для `previews` и `feed` (фактическая
длина списка, в том числе когда он передан переменной). `feed` по максимальным
100 каналам стоит около 1 000 + его поля, поэтому укладывается в лимит по
умолчанию, а два таких — уже нет.

Лимиты применяются к каждой операции пакета. Ограничение частоты запросов
([`ENABLE_RATE_LIMIT`](../configuration.md#enable_rate_limit)) считает
HTTP-запросы, поэтому пакет считается один раз.

## Ошибки { #errors }

Ошибки GraphQL возвращаются с HTTP `200` в списке `errors`, у каждой есть
стабильный `extensions.code`:

```json
{
  "data": {"body": null},
  "errors": [
    {
      "message": "Requested Telegram resource was not found",
      "locations": [{"line": 1, "column": 3}],
      "path": ["body"],
      "extensions": {"code": "NOT_FOUND"}
    }
  ]
}
```

| `extensions.code` | Статус в REST | Причина |
|---|---|---|
| `BAD_INPUT` | `422` | Некорректное имя пользователя, ID/позиция поста вне `1..10000000` или число каналов не в диапазоне 1–100 |
| `NOT_FOUND` | `404` | Канал или пост не существует (или канал приватный) |
| `BAD_GATEWAY` | `502` | t.me вернул страницу, которую парсер не смог разобрать |
| `UNAVAILABLE` | `503` | t.me недоступен или ответил ошибкой |
| `FEATURE_DISABLED` | `503` | `preview`/`previews` при `ENABLE_PREVIEWS=false`, `feed` при `ENABLE_FEED=false` |
| `QUERY_TOO_COMPLEX` | — | Операция стоит больше, чем `GRAPHQL_MAX_COMPLEXITY` |
| `GRAPHQL_PARSE_FAILED` | — | Синтаксическая ошибка или больше `GRAPHQL_MAX_TOKENS` токенов |
| `GRAPHQL_VALIDATION_FAILED` | — | Неизвестное поле или аргумент, неверный тип, лимит глубины/алиасов, интроспекция при выключенной документации |
| `INTERNAL_SERVER_ERROR` | `500` | Неожиданная ошибка сервера; сообщение скрывается, а подробности пишутся в лог |

Как и в REST, ввод проверяется раньше feature flags, поэтому некорректные
аргументы дают `BAD_INPUT`, даже когда функция отключена. Ошибки из строк
`BAD_INPUT`…`FEATURE_DISABLED` — это ошибки выполнения: они обнуляют только
своё поле. Остальные возникают до выполнения и возвращают `"data": null`.

Запросы, которые вообще не являются GraphQL, отклоняются с HTTP-статусом:
`400` для тела, которое не является JSON, не содержит `query` или представляет
собой слишком большой пакет; `400` для `GET`-запросов при выключенной
документации; `429` при срабатывании ограничения частоты запросов.
