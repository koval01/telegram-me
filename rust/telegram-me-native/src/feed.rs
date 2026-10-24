//! Feed assembly: cached channel pages in, the rendered `/v1/feed` body out.
//!
//! Python hands over the cached JSON of every channel page, exactly as the
//! page cache holds it, and gets the response bytes back; nothing in between
//! becomes a Python object. Pages are read with borrowed [`RawValue`]s: a
//! post is copied into the response as the JSON text it was cached as, and
//! only the few fields the ranker needs are decoded.
//!
//! The output is byte-for-byte what 2.0 produced with
//! `FeedResponse(result=...).model_dump_json()`: cached pages are pydantic-core
//! output, which `serde_json` reproduces exactly (same number formatting, same
//! string escaping), and every entry is `{**post, "channel": info, "_score":
//! score}` plus `_duplicates` and `_topic` when they apply, in that key order.
//!
//! The ranking signals follow the rules of 2.0's `services/feed_signals.py`.
//! Where that code raised on input our own parser never produces (a page that
//! is not an object, a post without an `id`), the page is reported as
//! unreadable and left out instead of failing the whole feed.
//!
//! Each page is read in one pass that only finds where values start and end;
//! a second, typed pass over each post's footer and content picks out the
//! ranking signals and skips everything else (HTML, entities, URLs).

use std::borrow::Cow;
use std::fmt;

use rayon::prelude::*;
use rustc_hash::FxHashSet as HashSet;
use serde::Deserialize;
use serde::de::value::MapAccessDeserializer;
use serde::de::{Deserializer, Error, MapAccess, Visitor};
use serde_json::Value;
use serde_json::value::RawValue;

use crate::parse::truthy;
use crate::rank::{self, Candidate, Options, Ranking};

const POSITIVE_REACTIONS: [&str; 10] = ["👍", "❤", "❤️", "😂", "🔥", "💯", "👏", "🎉", "🥰", "⭐"];
const NEGATIVE_REACTIONS: [&str; 6] = ["👎", "😡", "😢", "🤬", "🤡", "💩"];

/// One cached channel page.
#[derive(Debug, Clone, Copy)]
pub struct Page<'a> {
    /// The username the feed was requested with.
    pub channel: &'a str,
    /// The page as cached: the parser's JSON, UTF-8.
    pub json: &'a [u8],
}

/// The rendered feed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Feed {
    /// `{"result": [...]}`, the body of `POST /v1/feed`.
    pub json: Vec<u8>,
    /// Indices of pages that could not be read and were left out.
    pub unreadable: Vec<usize>,
}

/// Rank the posts of `pages` and render the feed response.
#[must_use]
pub fn build(pages: &[Page<'_>], options: &Options) -> Feed {
    let loaded: Vec<Option<Loaded<'_>>> = pages.par_iter().map(|page| load(*page)).collect();
    let unreadable = (0..pages.len()).filter(|&i| loaded[i].is_none()).collect();
    let mut loaded: Vec<Loaded<'_>> = loaded.into_iter().flatten().collect();
    let signals: Vec<Vec<Candidate>> = loaded
        .iter_mut()
        .map(|page| std::mem::take(&mut page.candidates))
        .collect();

    // Posts in page order; a post seen under the same username is dropped.
    let mut seen: HashSet<(&str, u64)> = HashSet::default();
    let mut entries: Vec<(usize, usize)> = Vec::new();
    let mut candidates: Vec<Candidate> = Vec::new();
    for ((page_index, page), signals) in loaded.iter().enumerate().zip(signals) {
        for ((post_index, post), candidate) in page.posts.iter().enumerate().zip(signals) {
            if seen.insert((page.username.as_str(), post.id)) {
                entries.push((page_index, post_index));
                candidates.push(candidate);
            }
        }
    }

    let ranking = rank::rank(&candidates, options);
    let json = render(&loaded, &entries, &ranking);
    Feed { json, unreadable }
}

/// A page reduced to what the feed needs.
struct Loaded<'a> {
    /// The channel profile, copied into every post as `channel`.
    info: Option<&'a RawValue>,
    /// `info["username"]`, as reported in `_duplicates`.
    info_username: Option<&'a RawValue>,
    /// Username the posts are ranked and de-duplicated under.
    username: String,
    posts: Vec<LoadedPost<'a>>,
    /// Ranker input of each post, taken out before ranking.
    candidates: Vec<Candidate>,
}

struct LoadedPost<'a> {
    id: u64,
    raw_id: &'a RawValue,
    fields: RawObject<'a>,
}

/// The parts of a cached page the feed reads, in one pass over the JSON.
#[derive(Deserialize)]
struct PageDoc<'a> {
    #[serde(borrow, default)]
    channel: Option<&'a RawValue>,
    #[serde(borrow, default)]
    content: Option<Object<ContentDoc<'a>>>,
}

#[derive(Deserialize)]
struct ContentDoc<'a> {
    #[serde(borrow, default)]
    posts: Option<Vec<RawObject<'a>>>,
}

fn load(page: Page<'_>) -> Option<Loaded<'_>> {
    let Object(doc) = serde_json::from_slice::<Object<PageDoc<'_>>>(page.json).ok()?;
    let info = match doc.channel {
        Some(raw) => Some((raw, RawObject::parse(raw)?)),
        None => None,
    };
    let info_username = info.as_ref().and_then(|(_, info)| info.get("username"));
    let username = match info_username.and_then(value).filter(truthy) {
        Some(username) => python_str(&username),
        None => page.channel.to_owned(),
    };
    let subscribers = info
        .as_ref()
        .and_then(|(_, info)| info.get("counters"))
        .and_then(RawObject::parse)
        .and_then(|counters| counters.get("subscribers"))
        .map_or(1, |raw| compact_number(raw, 1));

    let raw_posts = doc
        .content
        .and_then(|Object(content)| content.posts)
        .unwrap_or_default();
    let mut posts = Vec::with_capacity(raw_posts.len());
    let mut candidates = Vec::with_capacity(raw_posts.len());
    for fields in raw_posts {
        let raw_id = fields.get("id")?;
        let id = post_id(&value(raw_id)?)?;
        candidates.push(candidate(&username, subscribers, id, &fields));
        posts.push(LoadedPost { id, raw_id, fields });
    }
    Some(Loaded {
        info: info.map(|(raw, _)| raw),
        info_username,
        username,
        posts,
        candidates,
    })
}

/// `int(post["id"])` for the ids the ranker accepts (non-negative integers).
fn post_id(id: &Value) -> Option<u64> {
    match id {
        Value::Number(number) => number.as_u64(),
        Value::String(text) => text.trim().parse().ok(),
        _ => None,
    }
}

/// What the ranker reads from `post.footer`.
#[derive(Deserialize, Default)]
struct FooterSignals<'a> {
    #[serde(borrow, default)]
    views: Option<&'a RawValue>,
    #[serde(borrow, default)]
    edited: Option<&'a RawValue>,
    #[serde(borrow, default)]
    date: Option<Object<DateSignals<'a>>>,
}

#[derive(Deserialize)]
struct DateSignals<'a> {
    #[serde(borrow, default)]
    unix: Option<&'a RawValue>,
}

/// What the ranker reads from `post.content`; the text's HTML and entities
/// are skipped without being copied.
#[derive(Deserialize, Default)]
struct ContentSignals<'a> {
    #[serde(borrow, default)]
    text: Option<Object<TextSignals<'a>>>,
    #[serde(borrow, default)]
    media: Option<&'a RawValue>,
    #[serde(borrow, default)]
    reacts: Option<&'a RawValue>,
    #[serde(borrow, default)]
    poll: Option<&'a RawValue>,
}

#[derive(Deserialize)]
struct TextSignals<'a> {
    #[serde(borrow, default)]
    string: Option<Cow<'a, str>>,
}

/// Ranker input for one post (2.0: `feed_signals.candidate`).
///
/// A footer or content that does not have the parser's shape contributes no
/// signals (2.0 raised on most such values).
fn candidate(channel: &str, subscribers: u64, post_id: u64, post: &RawObject<'_>) -> Candidate {
    let signals = |key| post.get(key).map(RawValue::get);
    let Object(footer) = signals("footer")
        .and_then(|raw| serde_json::from_str::<Object<FooterSignals<'_>>>(raw).ok())
        .unwrap_or_default();
    let Object(content) = signals("content")
        .and_then(|raw| serde_json::from_str::<Object<ContentSignals<'_>>>(raw).ok())
        .unwrap_or_default();

    let published_at = footer
        .date
        .and_then(|Object(date)| date.unix)
        .and_then(value)
        .map_or(0, |unix| python_int(&unix));
    let (positive_reactions, negative_reactions) = content
        .reacts
        .and_then(value)
        .map_or((0, 0), |reacts| reaction_totals(&reacts));
    let (photos, videos, gifs) = content
        .media
        .and_then(value)
        .map_or((0, 0, 0), |media| media_counts(&media));
    let text = content
        .text
        .and_then(|Object(text)| text.string)
        .map(Cow::into_owned)
        .unwrap_or_default();

    Candidate {
        channel: channel.to_owned(),
        post_id,
        published_at,
        views: footer.views.map_or(0, |raw| compact_number(raw, 0)),
        subscribers,
        positive_reactions,
        negative_reactions,
        photos,
        videos,
        gifs,
        polls: u32::from(content.poll.is_some_and(raw_truthy)),
        text,
        edited: footer.edited.is_some_and(raw_truthy),
    }
}

/// Parse Telegram's compact counters such as `1.2K`, `3M` or `15 400`.
///
/// Spaces (also no-break spaces) are ignored; `default` is returned for an
/// empty or unparsable value. Only ASCII digits count as digits.
#[must_use]
#[expect(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "`int(float)`: finite, truncated and clamped at 0 first; saturates above u64"
)]
pub fn parse_compact_number(value: &str, default: u64) -> u64 {
    if value.is_empty() {
        return default;
    }
    let cleaned = value
        .chars()
        .filter(|c| !matches!(c, ' ' | '\u{a0}'))
        .collect::<String>()
        .to_lowercase();
    if !cleaned.is_empty() && cleaned.bytes().all(|b| b.is_ascii_digit()) {
        return cleaned.parse().unwrap_or(default);
    }
    let scaled = |number: &str, factor: f64| {
        let number = number.parse::<f64>().ok()? * factor;
        // `int(float(...) * factor)` truncates towards zero.
        number.is_finite().then(|| number.trunc().max(0.0) as u64)
    };
    let parsed = if let Some(number) = cleaned.strip_suffix('k') {
        scaled(number, 1_000.0)
    } else if let Some(number) = cleaned.strip_suffix('m') {
        scaled(number, 1_000_000.0)
    } else {
        cleaned.parse().ok()
    };
    parsed.unwrap_or(default)
}

/// `parse_compact_number(value)` for a raw JSON value; falsy values give `default`.
fn compact_number(raw: &RawValue, default: u64) -> u64 {
    match value(raw) {
        Some(value) if truthy(&value) => parse_compact_number(&python_str(&value), default),
        _ => default,
    }
}

/// Sums of positive and of negative reaction counters (Stars count as positive).
fn reaction_totals(reacts: &Value) -> (u64, u64) {
    let (mut positive, mut negative) = (0_u64, 0_u64);
    for react in reacts.as_array().into_iter().flatten() {
        let count = react
            .get("count")
            .filter(|count| truthy(count))
            .map_or(0, |count| parse_compact_number(&python_str(count), 0));
        let emoji = react.get("emoji").and_then(Value::as_str).unwrap_or("");
        let stars = react.get("type").and_then(Value::as_str) == Some("telegram_stars");
        if stars || POSITIVE_REACTIONS.contains(&emoji) {
            positive = positive.saturating_add(count);
        } else if NEGATIVE_REACTIONS.contains(&emoji) {
            negative = negative.saturating_add(count);
        }
    }
    (positive, negative)
}

/// Photos, videos (round ones included) and GIFs or stickers attached to a post.
fn media_counts(media: &Value) -> (u32, u32, u32) {
    let (mut photos, mut videos, mut gifs) = (0_u32, 0_u32, 0_u32);
    for item in media.as_array().into_iter().flatten() {
        match item.get("type").and_then(Value::as_str) {
            Some("image") => photos += 1,
            Some("video" | "roundvideo") => videos += 1,
            Some("gif" | "sticker") => gifs += 1,
            _ => {}
        }
    }
    (photos, videos, gifs)
}

/// `int(value)` for a JSON scalar; 0 when Python would not convert it.
#[expect(
    clippy::cast_possible_truncation,
    reason = "`int(float)` truncates; the cast saturates where Python would not"
)]
fn python_int(value: &Value) -> i64 {
    match value {
        Value::Number(number) => number
            .as_i64()
            .unwrap_or_else(|| number.as_f64().map_or(0, |n| n.trunc() as i64)),
        Value::String(text) => text.trim().parse().unwrap_or(0),
        Value::Bool(flag) => i64::from(*flag),
        _ => 0,
    }
}

/// `str(value)`: strings as they are, other values as JSON text.
fn python_str(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

fn value(raw: &RawValue) -> Option<Value> {
    serde_json::from_str(raw.get()).ok()
}

fn raw_truthy(raw: &RawValue) -> bool {
    value(raw).is_some_and(|value| truthy(&value))
}

// ---------------------------------------------------------------------------
// Output

/// A value the feed sets on a post, overriding the post's own key if present.
enum Field<'a> {
    Raw(&'a str),
    Score(f64),
    Duplicates(&'a [usize]),
    Topic(usize, &'a [String]),
}

fn render(loaded: &[Loaded<'_>], entries: &[(usize, usize)], ranking: &Ranking) -> Vec<u8> {
    let size: usize = entries
        .iter()
        .map(|&(page, post)| {
            let page = &loaded[page];
            page.posts[post].fields.size() + page.info.map_or(2, |info| info.get().len()) + 64
        })
        .sum();
    let mut out = Vec::with_capacity(size + 16);
    out.extend_from_slice(b"{\"result\":[");
    for (position, item) in ranking.items.iter().enumerate() {
        if position > 0 {
            out.push(b',');
        }
        let (page_index, post_index) = entries[item.index];
        let page = &loaded[page_index];
        let mut fields = vec![
            ("channel", Field::Raw(page.info.map_or("{}", RawValue::get))),
            ("_score", Field::Score(item.score)),
        ];
        if !item.duplicates.is_empty() {
            fields.push(("_duplicates", Field::Duplicates(&item.duplicates)));
        }
        if let Some(topic) = item.topic {
            let topic_info = &ranking.topics[topic];
            if topic_info.members.len() > 1 {
                fields.push(("_topic", Field::Topic(topic, &topic_info.keywords)));
            }
        }
        let post = &page.posts[post_index];
        write_object(&mut out, &post.fields, &fields, |out, field| match field {
            Field::Raw(text) => out.extend_from_slice(text.as_bytes()),
            Field::Score(score) => write_json(out, score),
            Field::Duplicates(duplicates) => {
                out.push(b'[');
                for (n, &duplicate) in duplicates.iter().enumerate() {
                    if n > 0 {
                        out.push(b',');
                    }
                    let (page_index, post_index) = entries[duplicate];
                    let page = &loaded[page_index];
                    out.extend_from_slice(b"{\"channel\":");
                    let username = page.info_username.map_or("null", RawValue::get);
                    out.extend_from_slice(username.as_bytes());
                    out.extend_from_slice(b",\"id\":");
                    out.extend_from_slice(page.posts[post_index].raw_id.get().as_bytes());
                    out.push(b'}');
                }
                out.push(b']');
            }
            Field::Topic(id, keywords) => {
                out.extend_from_slice(b"{\"id\":");
                write_json(out, id);
                out.extend_from_slice(b",\"keywords\":");
                write_json(out, keywords);
                out.push(b'}');
            }
        });
    }
    out.extend_from_slice(b"]}");
    out
}

/// `{**object, **fields}` as JSON: a key of `fields` replaces the object's own
/// value in place, the others are appended in order (Python dict semantics).
fn write_object(
    out: &mut Vec<u8>,
    object: &RawObject<'_>,
    fields: &[(&str, Field<'_>)],
    write_field: impl Fn(&mut Vec<u8>, &Field<'_>),
) {
    let mut written = vec![false; fields.len()];
    out.push(b'{');
    let mut first = true;
    let mut separator = |out: &mut Vec<u8>| {
        if !std::mem::take(&mut first) {
            out.push(b',');
        }
    };
    for (key, value) in &object.0 {
        separator(out);
        write_json(out, key.as_ref());
        out.push(b':');
        match fields.iter().position(|(name, _)| name == key) {
            Some(index) => {
                written[index] = true;
                write_field(out, &fields[index].1);
            }
            None => out.extend_from_slice(value.get().as_bytes()),
        }
    }
    for ((name, field), done) in fields.iter().zip(written) {
        if !done {
            separator(out);
            write_json(out, name);
            out.push(b':');
            write_field(out, field);
        }
    }
    out.push(b'}');
}

fn write_json(out: &mut Vec<u8>, value: &(impl serde::Serialize + ?Sized)) {
    serde_json::to_writer(&mut *out, value).expect("writing JSON to a Vec never fails");
}

// ---------------------------------------------------------------------------
// Borrowed JSON objects

/// A JSON object with its values kept as raw JSON text, in document order.
struct RawObject<'a>(Vec<(Cow<'a, str>, &'a RawValue)>);

impl<'a> RawObject<'a> {
    /// The object `raw` holds, or `None` when it holds something else.
    fn parse(raw: &'a RawValue) -> Option<Self> {
        serde_json::from_str(raw.get()).ok()
    }

    /// Value of `key`; with repeated keys the last one wins, as in Python.
    fn get(&self, key: &str) -> Option<&'a RawValue> {
        self.0
            .iter()
            .rev()
            .find(|(name, _)| name == key)
            .map(|(_, value)| *value)
    }

    /// Approximate length of the object as JSON.
    fn size(&self) -> usize {
        self.0
            .iter()
            .map(|(key, value)| key.len() + value.get().len() + 4)
            .sum()
    }
}

impl<'de: 'a, 'a> Deserialize<'de> for RawObject<'a> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor<'a>(std::marker::PhantomData<&'a ()>);

        impl<'de: 'a, 'a> Visitor<'de> for ObjectVisitor<'a> {
            type Value = RawObject<'a>;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object")
            }

            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut fields = Vec::with_capacity(map.size_hint().unwrap_or(8));
                while let Some((Key(key), value)) = map.next_entry::<Key<'de>, &'de RawValue>()? {
                    fields.push((key, value));
                }
                Ok(RawObject(fields))
            }
        }

        deserializer.deserialize_map(ObjectVisitor(std::marker::PhantomData))
    }
}

/// A `T` read from a JSON object only: derived structs would also accept an
/// array of their fields' values.
#[derive(Default)]
struct Object<T>(T);

impl<'de, T: Deserialize<'de>> Deserialize<'de> for Object<T> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor<T>(std::marker::PhantomData<T>);

        impl<'de, T: Deserialize<'de>> Visitor<'de> for ObjectVisitor<T> {
            type Value = Object<T>;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object")
            }

            fn visit_map<A: MapAccess<'de>>(self, map: A) -> Result<Self::Value, A::Error> {
                T::deserialize(MapAccessDeserializer::new(map)).map(Object)
            }
        }

        deserializer.deserialize_map(ObjectVisitor(std::marker::PhantomData))
    }
}

/// An object key, borrowed from the input unless it contains escapes.
struct Key<'a>(Cow<'a, str>);

impl<'de> Deserialize<'de> for Key<'de> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct KeyVisitor;

        impl<'de> Visitor<'de> for KeyVisitor {
            type Value = Key<'de>;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a string")
            }

            fn visit_borrowed_str<E: Error>(self, value: &'de str) -> Result<Self::Value, E> {
                Ok(Key(Cow::Borrowed(value)))
            }

            fn visit_str<E: Error>(self, value: &str) -> Result<Self::Value, E> {
                Ok(Key(Cow::Owned(value.to_owned())))
            }

            fn visit_string<E: Error>(self, value: String) -> Result<Self::Value, E> {
                Ok(Key(Cow::Owned(value)))
            }
        }

        deserializer.deserialize_str(KeyVisitor)
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    const NOW: i64 = 1_800_000_000;

    fn options() -> Options {
        Options {
            now: NOW,
            ..Options::default()
        }
    }

    fn feed(pages: &[(&str, Value)]) -> (Value, Vec<usize>) {
        let encoded: Vec<(&str, Vec<u8>)> = pages
            .iter()
            .map(|(channel, page)| (*channel, serde_json::to_vec(page).unwrap()))
            .collect();
        let pages: Vec<Page<'_>> = encoded
            .iter()
            .map(|(channel, json)| Page { channel, json })
            .collect();
        let built = build(&pages, &options());
        (
            serde_json::from_slice(&built.json).unwrap(),
            built.unreadable,
        )
    }

    fn post(id: u64, text: &str) -> Value {
        json!({
            "id": id,
            "content": {"text": {"string": text, "html": text}},
            "footer": {"views": "1.2K", "date": {"string": "x", "unix": NOW - 3600}},
            "view": "v",
        })
    }

    #[test]
    fn compact_numbers() {
        for (value, expected) in [
            ("15", 15),
            ("1.2K", 1200),
            ("3.5M", 3_500_000),
            ("15 400", 15400),
            ("10\u{a0}281", 10281),
            ("1.5k", 1500),
            ("+7", 7),
            ("n/a", 0),
            ("", 0),
            ("-5", 0),
            ("nan", 0),
            ("infk", 0),
        ] {
            assert_eq!(parse_compact_number(value, 0), expected, "{value}");
        }
        assert_eq!(parse_compact_number("", 1), 1);
        assert_eq!(parse_compact_number("abc", 7), 7);
    }

    #[test]
    fn reactions_are_split_by_sentiment() {
        let reacts = json!([
            {"type": "emoji", "emoji": "👍", "count": "1.2K"},
            {"type": "emoji", "emoji": "🔥", "count": "300"},
            {"type": "telegram_stars", "emoji": "⭐", "count": "50"},
            {"type": "emoji", "emoji": "👎", "count": "20"},
            {"type": "custom_emoji", "emoji": null, "emoji_id": "1", "count": "999"},
            {"type": "emoji", "emoji": "😡", "count": 5},
        ]);
        assert_eq!(reaction_totals(&reacts), (1550, 25));
        assert_eq!(reaction_totals(&Value::Null), (0, 0));
    }

    #[test]
    fn media_is_counted_by_kind() {
        let media = json!([
            {"type": "image"}, {"type": "image"}, {"type": "video"}, {"type": "roundvideo"},
            {"type": "gif"}, {"type": "sticker"}, {"type": "voice"},
        ]);
        assert_eq!(media_counts(&media), (2, 2, 2));
    }

    #[test]
    fn candidate_reads_the_signals() {
        let raw = serde_json::to_string(&json!({
            "id": 42,
            "content": {
                "text": {"string": "hello", "html": "hello"},
                "poll": {"question": "?"},
                "media": [{"type": "image"}],
                "reacts": [{"type": "emoji", "emoji": "👍", "count": "10"}],
            },
            "footer": {"views": "1.5K", "edited": true, "date": {"string": "x", "unix": NOW}},
        }))
        .unwrap();
        let fields: RawObject<'_> = serde_json::from_str(&raw).unwrap();
        let built = candidate("durov", 1000, 42, &fields);
        assert_eq!(
            (built.views, built.published_at, built.text.as_str()),
            (1500, NOW, "hello")
        );
        assert_eq!(
            (built.photos, built.polls, built.positive_reactions),
            (1, 1, 10)
        );
        assert!(built.edited);

        let fields: RawObject<'_> = serde_json::from_str(r#"{"id": 1}"#).unwrap();
        let empty = candidate("a", 1, 1, &fields);
        assert_eq!(
            (empty.views, empty.published_at, empty.text.as_str()),
            (0, 0, "")
        );
    }

    #[test]
    fn entries_carry_channel_and_score_in_order() {
        let page = json!({
            "channel": {"username": "durov", "counters": {"subscribers": "1M"}},
            "content": {"posts": [post(1, "one"), post(2, "two")]},
        });
        let (result, unreadable) = feed(&[("durov", page)]);
        assert_eq!(unreadable, Vec::<usize>::new());
        let entries = result["result"].as_array().unwrap();
        assert_eq!(entries.len(), 2);
        let keys: Vec<&str> = entries[0]
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            keys,
            ["id", "content", "footer", "view", "channel", "_score"]
        );
        assert_eq!(entries[0]["channel"]["username"], "durov");
    }

    #[test]
    fn repeated_posts_of_one_username_are_dropped() {
        let page = json!({
            "channel": {"username": "durov"},
            "content": {"posts": [post(1, "one"), post(1, "again")]},
        });
        let (result, _) = feed(&[("durov", page.clone()), ("durov_alias", page)]);
        assert_eq!(result["result"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn unreadable_pages_are_reported_and_skipped() {
        let encoded: Vec<&[u8]> = vec![
            b"not json",
            b"[1, 2]",
            br#"{"channel": "not an object"}"#,
            br#"{"content": {"posts": [{"no": "id"}]}}"#,
            br#"{"content": [[{"id": 1}]]}"#,
            br#"{"channel": {"username": "ok"}, "content": {"posts": []}}"#,
        ];
        let pages: Vec<Page<'_>> = encoded
            .iter()
            .map(|json| Page { channel: "x", json })
            .collect();
        let built = build(&pages, &options());
        assert_eq!(built.unreadable, vec![0, 1, 2, 3, 4]);
        assert_eq!(built.json, b"{\"result\":[]}");
    }

    #[test]
    fn overridden_keys_keep_their_position() {
        let mut first = post(1, "one");
        first
            .as_object_mut()
            .unwrap()
            .shift_insert(0, "channel".into(), json!("mine"));
        let page = json!({"channel": {"username": "a"}, "content": {"posts": [first]}});
        let (result, _) = feed(&[("a", page)]);
        let keys: Vec<&String> = result["result"][0].as_object().unwrap().keys().collect();
        assert_eq!(keys[0], "channel");
        assert_eq!(result["result"][0]["channel"]["username"], "a");
    }

    #[test]
    fn missing_username_falls_back_to_the_requested_channel() {
        let story = "Министерство обороны сообщило о новых поставках техники в регион, \
                     поставки начнутся уже на следующей неделе и продлятся до конца месяца";
        let a = json!({"content": {"posts": [post(1, story)]}});
        let b = json!({"channel": {"username": "b"}, "content": {"posts": [post(1, story)]}});
        let (result, _) = feed(&[("a", a), ("b", b)]);
        let entries = result["result"].as_array().unwrap();
        // Same id under different usernames: both kept, then merged as duplicates.
        assert_eq!(entries.len(), 1);
        let duplicates = &entries[0]["_duplicates"];
        assert_eq!(duplicates.as_array().unwrap().len(), 1);
        // `_duplicates` reports the page's own username, null when it has none.
        let channels = [
            &entries[0]["channel"]["username"],
            &duplicates[0]["channel"],
        ];
        assert!(channels.contains(&&Value::Null));
    }
}
