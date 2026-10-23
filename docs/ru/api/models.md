# Модели

Все модели ответов в том виде, в каком они определены в
[`telegram_me/schemas`](https://github.com/koval01/telegram-me/tree/main/src/telegram_me/schemas).
Те же определения доступны как JSON Schema в `/openapi.json`.

Необязательные поля всегда присутствуют в ответах и равны `null`, если
значения нет ([лента](feed.md) возвращает сырые посты и вместо этого опускает
такие поля).

## ChannelBody

Возвращается [`/v1/body`](channel-page.md).

| Поле | Тип | Описание |
|---|---|---|
| `channel` | [Channel](#channel) | Профиль |
| `content.posts` | [Post](#post)[] | Посты на странице, от старых к новым |
| `meta` | [Meta](#meta) | Курсоры пагинации |

## More

Возвращается [`/v1/more`](more.md): `posts` ([Post](#post)[]) и `meta`
([Meta](#meta)).

## Channel

| Поле | Тип | Описание |
|---|---|---|
| `username` | string | Имя пользователя без `@` |
| `title` | [ParsedAndRaw](#parsedandraw) | Отображаемое имя |
| `description` | [ParsedAndRaw](#parsedandraw) \| null | Описание |
| `avatar` | URL \| null | Аватар |
| `counters.subscribers` | string | например `10.3M` |
| `counters.photos`, `videos`, `files`, `links` | string \| null | Счётчики медиа, которые показывает Telegram |
| `labels` | string[] \| null | Значки в шапке, например `verified` |

## Meta

| Поле | Тип | Описание |
|---|---|---|
| `offset.before` | integer \| null | Передайте в `/v1/more/{channel}/before/{value}`, чтобы получить более старые посты |
| `offset.after` | integer \| null | Передайте в `/v1/more/{channel}/after/{value}`, чтобы получить более новые посты |

## Post

| Поле | Тип | Описание |
|---|---|---|
| `id` | integer | ID поста внутри канала |
| `content` | [Content](#content) | Всё, что содержит пост |
| `footer` | [Footer](#footer) | Просмотры, дата, автор |
| `forwarded` | [Forwarded](#forwarded) \| null | Исходный источник пересылки |
| `view` | string | Непрозрачный токен просмотра от Telegram |

### Content

| Поле | Тип | Описание |
|---|---|---|
| `text` | [Text](#text) \| null | Текст сообщения |
| `media` | [MediaItem](#mediaitem)[] \| null | Фото, видео, голосовые сообщения, видеосообщения в кружке, стикеры |
| `poll` | [Poll](#poll) \| null | Опрос *(только полный парсер)* |
| `inline` | [Inline](#inline)[] \| null | Кнопки inline-клавиатуры, все ряды подряд по порядку |
| `reply` | [Reply](#reply) \| null | Сообщение, на которое дан ответ *(только полный парсер)* |
| `preview_link` | [PreviewLink](#previewlink) \| null | Карточка превью ссылки *(только полный парсер)* |
| `reacts` | [Reacts](#reacts)[] \| null | Реакции *(только полный парсер)* |

При [`PARSER_MODE=simplified`](../configuration.md#parser_mode) извлекаются
только `text` (без сущностей), `media` и `inline`.

### Text

| Поле | Тип | Описание |
|---|---|---|
| `string` | string | Простой текст, `\n` вместо переводов строк |
| `html` | string | Исходная разметка |
| `entities` | [TextEntity](#textentity)[] \| null | Форматирование *(только полный парсер)* |

### TextEntity

| Поле | Тип | Описание |
|---|---|---|
| `type` | enum | `bold`, `italic`, `underline`, `strikethrough`, `code`, `pre`, `spoiler`, `hashtag`, `url`, `text_link`, `emoji`, `animoji` |
| `offset` | integer | Начало в `Text.string` (UTF-16 **не** используется: индексы строк Python) |
| `length` | integer | Длина видимого текста |
| `url` | URI \| null | Адрес ссылки (`text_link`, `url`) или изображение эмодзи (`emoji`, `animoji`) |
| `language` | string \| null | Зарезервировано для блоков `pre` |

### MediaItem

| Поле | Тип | Описание |
|---|---|---|
| `type` | enum | `image`, `video`, `gif`, `voice`, `roundvideo`, `sticker` |
| `url` | URL \| null | URL файла; `null`, если Telegram его не раскрывает |
| `thumb` | URL \| null | Миниатюра (видео, видеосообщение в кружке, стикер) |
| `duration` | `{formatted, raw}` \| null | `formatted` — как показано (`1:15`), `raw` — в секундах |
| `waves` | string \| null | Закодированная форма волны голосового сообщения |
| `available` | boolean \| null | `false`, если файл слишком большой и t.me его не отдаёт |

Видео без длительности возвращается как `gif`.

### Poll

| Поле | Тип | Описание |
|---|---|---|
| `question` | string | Вопрос |
| `type` | string \| null | Как показывает Telegram, например `Anonymous Poll` |
| `votes` | string \| null | Счётчик голосов, как показан |
| `options[]` | `{name: string, percent: integer}` | Варианты ответа с их долей |

### Inline

| Поле | Тип | Описание |
|---|---|---|
| `title` | string | Текст кнопки |
| `url` | URI | Адрес (`https://…`, `tg://…`) |

### Reply

| Поле | Тип | Описание |
|---|---|---|
| `to_message` | integer | ID поста, на который дан ответ |
| `name` | [ParsedAndRaw](#parsedandraw) | Его автор |
| `text` | [ParsedAndRaw](#parsedandraw) | Его текст (фрагмент) |
| `cover` | URI \| null | Его миниатюра |

### PreviewLink

| Поле | Тип | Описание |
|---|---|---|
| `url` | URL | Страница по ссылке |
| `site_name`, `title` | string \| null | Заголовок карточки |
| `description` | [ParsedAndRaw](#parsedandraw) \| null | Текст карточки |
| `thumb` | URL \| null | Изображение карточки |

### Reacts

| Поле | Тип | Описание |
|---|---|---|
| `type` | enum | `emoji`, `custom_emoji`, `telegram_stars` |
| `count` | string | Счётчик, как показан (`14.2K`) |
| `emoji` | string \| null | Эмодзи (`⭐` для Stars) |
| `emoji_id` | string \| null | ID кастомного эмодзи |
| `emoji_image` | URL \| null | Спрайт эмодзи |

### Footer

| Поле | Тип | Описание |
|---|---|---|
| `views` | string \| null | Счётчик просмотров |
| `edited` | boolean | Пост редактировался |
| `author` | [ParsedAndRaw](#parsedandraw) \| null | Подпись, если канал подписывает посты |
| `date` | `{string, unix}` | Время публикации |

### Forwarded

| Поле | Тип | Описание |
|---|---|---|
| `name` | [ParsedAndRaw](#parsedandraw) | Канал или пользователь-источник; пустые строки, если отправитель скрывает имя |
| `url` | URL \| null | Ссылка на источник |
| `author` | [ParsedAndRaw](#parsedandraw) \| null | Подпись рядом с источником (добавлено в 2.0) |

## Preview

Возвращается [`/v1/preview`](previews.md): `{"channel": PreviewChannel}`.

| Поле | Тип | Описание |
|---|---|---|
| `title` | string | Название |
| `subscribers` | string | Как показано, например `10 281 023 subscribers` |
| `description` | string \| null | Описание простым текстом |
| `avatar` | URL \| null | Изображение |
| `is_verified` | boolean | Значок верификации |

`/v1/previews` возвращает `{username: Preview}`.

## FeedResponse

`{"result": [ … ]}` — см. [Лента](feed.md#response).

## ParsedAndRaw

`{"string": string, "html": string}` — простой текст и исходная разметка.
