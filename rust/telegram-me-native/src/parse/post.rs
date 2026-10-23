//! Channel posts (`.tgme_widget_message`).

use std::sync::LazyLock;

use regex::Regex;
use scraper::ElementRef;
use serde_json::{Map, Value};

use super::dom::{Document, attr, css, first, select, text, text_strip};
use super::entities::parse_entities;
use super::media::extract_media;
use super::reactions::parse_reactions;
use super::{background_url, inner_html, object, opt_str, truthy};

const FORMATTING_TAGS: &[&str] = &[
    "a",
    "i",
    "b",
    "s",
    "u",
    "pre",
    "code",
    "span",
    "tg-emoji",
    "tg-spoiler",
];

static LINE_BREAK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<br\s?/?>").expect("valid regex"));
static TEXT_DIV: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?s)<div\s+class="tgme_widget_message_text(?:[^"]*)"(?:\s+[^>]*?)?>(.*?)(?:</div>|$)"#,
    )
    .expect("valid regex")
});
static REPLY_TARGET: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"https://t\.me/[\w-]+/(\d+)").expect("valid regex"));

type Fallible<T> = Result<T, String>;

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let yoe = year - era * 400;
    let doy = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Seconds since the epoch of an ISO-8601 timestamp with an offset
/// (`2026-10-20T14:00:12+00:00`), like `datetime.fromisoformat().timestamp()`.
///
/// # Errors
/// When the timestamp is not in that format.
pub fn unix_timestamp(timestamp: &str) -> Fallible<i64> {
    let invalid = || format!("ValueError: Invalid isoformat string: {timestamp:?}");
    let number = |range: std::ops::Range<usize>| -> Fallible<i64> {
        timestamp
            .get(range)
            .and_then(|s| s.parse::<i64>().ok())
            .ok_or_else(invalid)
    };
    if timestamp.len() < 19 || !timestamp.is_ascii() {
        return Err(invalid());
    }
    let (year, month, day) = (number(0..4)?, number(5..7)?, number(8..10)?);
    let (hour, minute, second) = (number(11..13)?, number(14..16)?, number(17..19)?);
    let mut rest = &timestamp[19..];
    if let Some(fraction) = rest.strip_prefix('.') {
        let digits = fraction.chars().take_while(char::is_ascii_digit).count();
        rest = &fraction[digits..];
    }
    let offset = match rest {
        "" | "Z" => 0,
        _ => {
            let sign = match rest.as_bytes()[0] {
                b'+' => 1,
                b'-' => -1,
                _ => return Err(invalid()),
            };
            let digits: String = rest[1..].chars().filter(char::is_ascii_digit).collect();
            if digits.len() != 4 {
                return Err(invalid());
            }
            let hours: i64 = digits[0..2].parse().map_err(|_| invalid())?;
            let minutes: i64 = digits[2..4].parse().map_err(|_| invalid())?;
            sign * (hours * 3600 + minutes * 60)
        }
    };
    Ok(days_from_civil(year, month, day) * 86_400 + hour * 3600 + minute * 60 + second - offset)
}

fn plain_text(html: &str) -> String {
    let text = LINE_BREAK.replace_all(html, "\n");
    let text = TEXT_DIV
        .captures(&text)
        .map_or_else(|| text.to_string(), |c| c[1].to_owned());
    text.replace("&nbsp;", " ")
}

fn message_text(doc: &Document, bubble: ElementRef<'_>, with_entities: bool) -> Fallible<Value> {
    let Some(node) = first(bubble, css!(".tgme_widget_message_text, .js-message_text")) else {
        return Ok(Value::Null);
    };
    let html = inner_html(doc, Some(node), "div");
    doc.unwrap(node, FORMATTING_TAGS);
    let stripped = inner_html(doc, Some(node), "div");
    let (Some(html), Some(stripped)) = (html, stripped) else {
        return Ok(Value::Null);
    };

    let mut result = Map::new();
    result.insert("string".into(), Value::from(plain_text(&stripped)));
    if with_entities {
        let entities = parse_entities(&html)?;
        result.insert("html".into(), Value::from(html));
        if !entities.is_empty() {
            result.insert("entities".into(), Value::Array(entities));
        }
    } else {
        result.insert("html".into(), Value::from(html));
    }
    Ok(Value::Object(result))
}

fn inline_buttons(message: ElementRef<'_>) -> Value {
    let Some(keyboard) = first(message, css!(".tgme_widget_message_inline_keyboard")) else {
        return Value::Null;
    };
    let buttons = select(
        keyboard,
        css!(".tgme_widget_message_inline_row .tgme_widget_message_inline_button"),
    )
    .into_iter()
    .map(|button| {
        let title = first(button, css!("span")).map(text).unwrap_or_default();
        object! {"title" => title, "url" => opt_str(attr(button, "href"))}
    })
    .collect();
    Value::Array(buttons)
}

fn poll(bubble: ElementRef<'_>) -> Value {
    let Some(poll) = first(bubble, css!(".tgme_widget_message_poll")) else {
        return Value::Null;
    };
    let mut options = Vec::new();
    for option in select(poll, css!(".tgme_widget_message_poll_option")) {
        let name = first(option, css!(".tgme_widget_message_poll_option_text"));
        let percent = first(option, css!(".tgme_widget_message_poll_option_percent"));
        let (Some(name), Some(percent)) = (name, percent) else {
            continue;
        };
        let value = text(percent);
        let value = value.strip_suffix('%').unwrap_or(&value);
        if !value.is_empty()
            && value.chars().all(|c| c.is_ascii_digit())
            && let Ok(percent) = value.parse::<i64>()
        {
            options.push(object! {"name" => text(name), "percent" => percent});
        }
    }
    let question = first(poll, css!(".tgme_widget_message_poll_question")).map(text);
    let poll_type = first(poll, css!(".tgme_widget_message_poll_type")).map(text);
    let voters = first(bubble, css!(".tgme_widget_message_voters")).map(text);
    object! {
        "question" => opt_str(question),
        "type" => opt_str(poll_type),
        "votes" => opt_str(voters),
        "options" => options,
    }
}

fn preview_link(doc: &Document, message: ElementRef<'_>) -> Value {
    let Some(preview) = first(message, css!(".tgme_widget_message_link_preview")) else {
        return Value::Null;
    };
    let site_name = first(preview, css!(".link_preview_site_name"));
    let title = first(preview, css!(".link_preview_title"));
    let description = first(preview, css!(".link_preview_description"));
    let thumb = first(
        preview,
        css!(".tgme_widget_message_link_preview > .link_preview_right_image"),
    );
    object! {
        "site_name" => opt_str(site_name.map(text_strip)),
        "url" => opt_str(attr(preview, "href")),
        "title" => opt_str(title.map(text_strip)),
        "description" => description.map_or(Value::Null, |d| object! {
            "string" => text(d),
            "html" => opt_str(inner_html(doc, Some(d), "div")),
        }),
        "thumb" => opt_str(thumb.and_then(|t| background_url(attr(t, "style")))),
    }
}

fn reply(doc: &Document, message: ElementRef<'_>) -> Fallible<Value> {
    let Some(reply) = first(message, css!(".tgme_widget_message_reply")) else {
        return Ok(Value::Null);
    };
    let reply_text = first(
        reply,
        css!(".tgme_widget_message_metatext, .tgme_widget_message_text"),
    );
    let name = first(reply, css!(".tgme_widget_message_author"));
    let target = REPLY_TARGET.captures(attr(reply, "href").unwrap_or_default());
    let (Some(reply_text), Some(name), Some(target)) = (reply_text, name, target) else {
        return Ok(Value::Null);
    };
    let cover = first(reply, css!(".tgme_widget_message_reply_thumb"));
    let to_message: i64 = target[1]
        .parse()
        .map_err(|_| format!("ValueError: invalid literal {:?}", &target[1]))?;
    Ok(object! {
        "cover" => opt_str(cover.and_then(|c| background_url(attr(c, "style")))),
        "name" => object! {
            "string" => text(name).trim(),
            "html" => opt_str(inner_html(doc, Some(name), "span")),
        },
        "text" => object! {
            "string" => text(reply_text),
            "html" => opt_str(inner_html(doc, Some(reply_text), "div")),
        },
        "to_message" => to_message,
    })
}

fn name(doc: &Document, node: Option<ElementRef<'_>>) -> Value {
    node.map_or(Value::Null, |node| {
        object! {"string" => text(node), "html" => opt_str(inner_html(doc, Some(node), "span"))}
    })
}

fn footer(doc: &Document, bubble: ElementRef<'_>) -> Fallible<Value> {
    let time = first(bubble, css!(".tgme_widget_message_date > time"));
    let timestamp = time
        .and_then(|t| attr(t, "datetime"))
        .filter(|t| !t.is_empty());
    let Some(timestamp) = timestamp else {
        return Err("ValueError: post has no publication time".into());
    };
    let views = first(bubble, css!(".tgme_widget_message_views")).map(text);
    let meta = first(bubble, css!(".tgme_widget_message_meta"));
    let mut footer = object! {
        "views" => opt_str(views),
        "edited" => meta.is_some_and(|m| text(m).starts_with("edited")),
        "date" => object! {"string" => timestamp, "unix" => unix_timestamp(timestamp)?},
    };
    let author = name(doc, first(bubble, css!(".tgme_widget_message_from_author")));
    if truthy(&author) {
        footer
            .as_object_mut()
            .expect("object")
            .insert("author".into(), author);
    }
    Ok(footer)
}

fn forwarded(doc: &Document, message: ElementRef<'_>) -> Value {
    let Some(node) = first(message, css!(".tgme_widget_message_forwarded_from_name")) else {
        return Value::Null;
    };
    // The visible name sits in a nested <span dir="auto">; reading it directly
    // keeps `html` well-formed (it is empty for senders who hide their name).
    let label = first(node, css!("span[dir=auto]")).unwrap_or(node);
    let mut forwarded = object! {
        "name" => object! {
            "string" => text(node),
            "html" => inner_html(doc, Some(label), "span").unwrap_or_default(),
        },
    };
    let map = forwarded.as_object_mut().expect("object");
    if let Some(url) = attr(node, "href").filter(|u| !u.is_empty()) {
        map.insert("url".into(), Value::from(url));
    }
    if let Some(author) = first(message, css!(".tgme_widget_message_forwarded_from_author")) {
        map.insert(
            "author".into(),
            object! {
                "string" => text(author),
                "html" => inner_html(doc, Some(author), "span").unwrap_or_default(),
            },
        );
    }
    forwarded
}

/// Numeric post id from `data-post="<channel>/<id>"`.
///
/// # Errors
/// When the attribute is missing or malformed.
pub fn post_id(message: ElementRef<'_>) -> Fallible<i64> {
    let data_post = attr(message, "data-post").unwrap_or_default();
    let part = data_post
        .split('/')
        .nth(1)
        .ok_or_else(|| "IndexError: list index out of range".to_owned())?;
    part.trim()
        .parse()
        .map_err(|_| format!("ValueError: invalid literal for int() with base 10: {part:?}"))
}

fn parse_message(
    doc: &Document,
    message: ElementRef<'_>,
    identifier: i64,
    simplified: bool,
) -> Fallible<Value> {
    let bubble = first(message, css!(".tgme_widget_message_bubble"))
        .ok_or_else(|| "ValueError: post has no message bubble".to_owned())?;

    let fields: Vec<(&str, Value)> = if simplified {
        vec![
            ("text", message_text(doc, bubble, false)?),
            ("media", Value::Array(extract_media(bubble))),
            ("inline", inline_buttons(message)),
        ]
    } else {
        vec![
            ("text", message_text(doc, bubble, true)?),
            ("media", Value::Array(extract_media(bubble))),
            ("poll", poll(bubble)),
            ("inline", inline_buttons(message)),
            ("reply", reply(doc, message)?),
            ("preview_link", preview_link(doc, message)),
            ("reacts", parse_reactions(message)),
        ]
    };
    let mut content = Map::new();
    for (key, value) in fields {
        if truthy(&value) {
            content.insert(key.to_owned(), value);
        }
    }

    let mut post = object! {
        "id" => identifier,
        "content" => Value::Object(content),
        "footer" => footer(doc, bubble)?,
        "view" => opt_str(attr(message, "data-view")),
    };
    let forwarded = forwarded(doc, message);
    if truthy(&forwarded) {
        post.as_object_mut()
            .expect("object")
            .insert("forwarded".into(), forwarded);
    }
    Ok(post)
}

/// Parse every post under `root`; malformed posts are skipped and reported.
pub fn parse_posts(
    doc: &Document,
    root: ElementRef<'_>,
    simplified: bool,
    skipped: &mut Vec<(String, String)>,
) -> Vec<Value> {
    let mut posts = Vec::new();
    for message in select(
        root,
        css!(".tgme_widget_message_wrap > .tgme_widget_message"),
    ) {
        match post_id(message).and_then(|id| parse_message(doc, message, id, simplified)) {
            Ok(post) => posts.push(post),
            Err(reason) => skipped.push((
                attr(message, "data-post").unwrap_or("None").to_owned(),
                reason,
            )),
        }
    }
    posts
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamps() {
        assert_eq!(
            unix_timestamp("2026-10-20T10:00:00+00:00"),
            Ok(1_792_490_400)
        );
        assert_eq!(
            unix_timestamp("2026-10-20T10:00:00+0000"),
            Ok(1_792_490_400)
        );
        assert_eq!(
            unix_timestamp("2026-10-20T13:00:00+03:00"),
            Ok(1_792_490_400)
        );
        assert_eq!(unix_timestamp("1970-01-01T00:00:00Z"), Ok(0));
        assert!(unix_timestamp("not a date").is_err());
    }

    #[test]
    fn plain_text_handles_breaks_and_nbsp() {
        assert_eq!(plain_text("a<br>b<br/>c&nbsp;d"), "a\nb\nc d");
    }
}
