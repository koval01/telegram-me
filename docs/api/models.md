# Models

All response models, as defined in
[`telegram_me/schemas`](https://github.com/koval01/telegram-me/tree/main/src/telegram_me/schemas).
The same definitions are available as JSON Schema in `/openapi.json`.

Optional fields are always present in responses and set to `null` when
missing (the [feed](feed.md) returns raw posts and omits them instead).

## ChannelBody

Returned by [`/v1/body`](channel-page.md).

| Field | Type | Description |
|---|---|---|
| `channel` | [Channel](#channel) | Profile |
| `content.posts` | [Post](#post)[] | Posts on the page, oldest first |
| `meta` | [Meta](#meta) | Pagination cursors |

## More

Returned by [`/v1/more`](more.md): `posts` ([Post](#post)[]) and `meta` ([Meta](#meta)).

## Channel

| Field | Type | Description |
|---|---|---|
| `username` | string | Username without `@` |
| `title` | [ParsedAndRaw](#parsedandraw) | Display name |
| `description` | [ParsedAndRaw](#parsedandraw) \| null | Bio |
| `avatar` | URL \| null | Profile picture |
| `counters.subscribers` | string | e.g. `10.3M` |
| `counters.photos`, `videos`, `files`, `links` | string \| null | Media counters shown by Telegram |
| `labels` | string[] \| null | Header badges, e.g. `verified` |

## Meta

| Field | Type | Description |
|---|---|---|
| `offset.before` | integer \| null | Pass to `/v1/more/{channel}/before/{value}` for older posts |
| `offset.after` | integer \| null | Pass to `/v1/more/{channel}/after/{value}` for newer posts |

## Post

| Field | Type | Description |
|---|---|---|
| `id` | integer | Post ID within the channel |
| `content` | [Content](#content) | Everything the post contains |
| `footer` | [Footer](#footer) | Views, date, author |
| `forwarded` | [Forwarded](#forwarded) \| null | Original source of a forward |
| `view` | string | Telegram's opaque view token |

### Content

| Field | Type | Description |
|---|---|---|
| `text` | [Text](#text) \| null | Message text |
| `media` | [MediaItem](#mediaitem)[] \| null | Photos, videos, voice messages, round videos, stickers |
| `poll` | [Poll](#poll) \| null | Poll *(full parser only)* |
| `inline` | [Inline](#inline)[] \| null | Inline keyboard buttons, all rows flattened in order |
| `reply` | [Reply](#reply) \| null | Replied-to message *(full parser only)* |
| `preview_link` | [PreviewLink](#previewlink) \| null | Link preview card *(full parser only)* |
| `reacts` | [Reacts](#reacts)[] \| null | Reactions *(full parser only)* |

With [`PARSER_MODE=simplified`](../configuration.md#parser_mode) only `text`
(without entities), `media` and `inline` are extracted.

### Text

| Field | Type | Description |
|---|---|---|
| `string` | string | Plain text, `\n` for line breaks |
| `html` | string | Original markup |
| `entities` | [TextEntity](#textentity)[] \| null | Formatting *(full parser only)* |

### TextEntity

| Field | Type | Description |
|---|---|---|
| `type` | enum | `bold`, `italic`, `underline`, `strikethrough`, `code`, `pre`, `spoiler`, `hashtag`, `url`, `text_link`, `emoji`, `animoji` |
| `offset` | integer | Start in `Text.string` (UTF-16 is **not** used: Python string indices) |
| `length` | integer | Length of the visible text |
| `url` | URI \| null | Link target (`text_link`, `url`) or emoji image (`emoji`, `animoji`) |
| `language` | string \| null | Reserved for `pre` blocks |

### MediaItem

| Field | Type | Description |
|---|---|---|
| `type` | enum | `image`, `video`, `gif`, `voice`, `roundvideo`, `sticker` |
| `url` | URL \| null | File URL; `null` when Telegram does not expose it |
| `thumb` | URL \| null | Thumbnail (video, round video, sticker) |
| `duration` | `{formatted, raw}` \| null | `formatted` as shown (`1:15`), `raw` in seconds |
| `waves` | string \| null | Encoded waveform of a voice message |
| `available` | boolean \| null | `false` when the file is too large to be served by t.me |

A video without a duration is reported as `gif`.

### Poll

| Field | Type | Description |
|---|---|---|
| `question` | string | Question |
| `type` | string \| null | As shown by Telegram, e.g. `Anonymous Poll` |
| `votes` | string \| null | Vote counter as shown |
| `options[]` | `{name: string, percent: integer}` | Answers with their share |

### Inline

| Field | Type | Description |
|---|---|---|
| `title` | string | Button text |
| `url` | URI | Target (`https://…`, `tg://…`) |

### Reply

| Field | Type | Description |
|---|---|---|
| `to_message` | integer | ID of the replied-to post |
| `name` | [ParsedAndRaw](#parsedandraw) | Its author |
| `text` | [ParsedAndRaw](#parsedandraw) | Its text (preview) |
| `cover` | URI \| null | Its thumbnail |

### PreviewLink

| Field | Type | Description |
|---|---|---|
| `url` | URL | Linked page |
| `site_name`, `title` | string \| null | Card header |
| `description` | [ParsedAndRaw](#parsedandraw) \| null | Card text |
| `thumb` | URL \| null | Card image |

### Reacts

| Field | Type | Description |
|---|---|---|
| `type` | enum | `emoji`, `custom_emoji`, `telegram_stars` |
| `count` | string | Counter as shown (`14.2K`) |
| `emoji` | string \| null | The emoji (`⭐` for Stars) |
| `emoji_id` | string \| null | Custom emoji ID |
| `emoji_image` | URL \| null | Emoji sprite |

### Footer

| Field | Type | Description |
|---|---|---|
| `views` | string \| null | View counter |
| `edited` | boolean | Post was edited |
| `author` | [ParsedAndRaw](#parsedandraw) \| null | Signature, when the channel signs posts |
| `date` | `{string, unix}` | Publication time |

### Forwarded

| Field | Type | Description |
|---|---|---|
| `name` | [ParsedAndRaw](#parsedandraw) | Source channel or user; empty strings when the sender hides their name |
| `url` | URL \| null | Link to the source |
| `author` | [ParsedAndRaw](#parsedandraw) \| null | Signature next to the source (added in 2.0) |

## Preview

Returned by [`/v1/preview`](previews.md): `{"channel": PreviewChannel}`.

| Field | Type | Description |
|---|---|---|
| `title` | string | Name |
| `subscribers` | string | As displayed, e.g. `10 281 023 subscribers` |
| `description` | string \| null | Plain-text bio |
| `avatar` | URL \| null | Picture |
| `is_verified` | boolean | Verified badge |

`/v1/previews` returns `{username: Preview}`.

## FeedResponse

`{"result": [ … ]}` — see [Feed](feed.md#response).

## ParsedAndRaw

`{"string": string, "html": string}` — plain text and original markup.
