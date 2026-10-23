# Parsing t.me

## Pages

| Page | URL | Parser |
|---|---|---|
| Channel feed | `GET https://t.me/s/{channel}[/{position}]` | `parse_channel_page` |
| Infinite scroll | `POST https://t.me/s/{channel}?before=…` / `?after=…` with `X-Requested-With: XMLHttpRequest` — returns a JSON string with an HTML fragment | `parse_more_page` |
| Landing page | `GET https://t.me/{channel}` | `parse_preview_page` |

Requests are sent with the headers of a mobile Chrome. Redirects are not
followed: an unknown channel redirects `/s/{channel}` to `/{channel}`, which is
then reported as `404`.

## Extraction

Parsing is implemented in Rust
([`rust/telegram-me-native/src/parse`](https://github.com/koval01/telegram-me/tree/main/rust/telegram-me-native/src/parse))
and runs with the GIL released: the HTML is parsed once with html5ever (the
WHATWG-compliant parser of the Servo project) and walked with CSS selectors.
Python only decodes the resulting JSON (pydantic-core) and logs skipped posts.

The Rust port reproduces the original Python/selectolax parser byte for byte:
selectors match the node itself as well as its descendants, text is
concatenated exactly like lexbor's `text()`, HTML is re-serialised with
lexbor's escaping rules and attribute order, and formatting tags unwrapped for
the plain text stay unwrapped for later reads of the same subtree, as with
selectolax's in-place `unwrap_tags`. The contract snapshots pin the result.

Per post (`.tgme_widget_message_wrap > .tgme_widget_message`):

| Field | Source |
|---|---|
| `id` | `data-post="<channel>/<id>"` |
| `text` | `.tgme_widget_message_text` — `html` is the inner markup; `string` the text with formatting tags unwrapped and `<br>` as `\n` |
| `entities` | regular expressions over the text markup (bold, italic, underline, strikethrough, code, spoiler, hashtags, links, emoji, custom emoji); alternatives without look-ahead run on the `regex` DFA engine, the three with look-ahead are checked cheaply and fall back to `fancy-regex` only when needed |
| `media` | photo wraps, video/round video players, voice players, stickers (`picture`, `i`, `video` variants), link preview images |
| `poll` | `.tgme_widget_message_poll` question, type, options with percentages, voters |
| `inline` | every `.tgme_widget_message_inline_button` in the inline keyboard |
| `reply` | `.tgme_widget_message_reply`: author, text, thumbnail, target post from its link |
| `preview_link` | `.tgme_widget_message_link_preview` |
| `reacts` | `.tgme_reaction`: Telegram Stars, standard emoji (with sprite URL), custom emoji (`emoji-id`) |
| `footer` | `time[datetime]`, views, "edited" marker, author signature |
| `forwarded` | `.tgme_widget_message_forwarded_from_name` and `_author` |

## Robustness

- Each post is parsed in isolation. If anything about a post is unexpected,
  that post is logged (`Skipping unparsable post <channel>/<id>`) and left
  out; the rest of the page is returned.
- After parsing, the page is validated against the response models. Posts that
  do not validate are dropped the same way, so one odd post never turns the
  whole response into an error.
- Only when the page as a whole makes no sense is a `502` returned.

## When Telegram changes its markup

1. Reproduce with `uv run python scripts/record_fixtures.py <channel>` to
   record the new markup.
2. Fix the parser in `rust/telegram-me-native/src/parse/` and run
   `make rust` and `make native`.
3. Run `make snapshots` and review the snapshot diff — it shows exactly which
   responses changed.
