//! Parsing of t.me pages into JSON.
//!
//! A faithful port of the original Python/selectolax parser: the produced JSON
//! is byte-for-byte identical (pinned by the contract snapshots), only faster.
//! Per-post failures are isolated: a malformed post is skipped and reported in
//! [`Parsed::skipped`] instead of failing the page.

pub mod dom;
pub mod entities;
pub mod media;
pub mod pages;
pub mod post;
pub mod reactions;

use std::sync::LazyLock;

use regex::Regex;
use scraper::ElementRef;
use serde_json::{Map, Value};

use dom::{Document, attr, css, first, select, text};

pub use pages::{parse_channel_page, parse_more_page, parse_preview_page};

/// A parsed page and the posts that had to be skipped.
#[derive(Debug, Clone, PartialEq)]
pub struct Parsed {
    /// The page as a JSON object.
    pub value: Value,
    /// `(data-post, reason)` of every skipped post.
    pub skipped: Vec<(String, String)>,
}

/// JSON object literal preserving key order.
macro_rules! object {
    ($($key:expr => $value:expr),* $(,)?) => {{
        #[allow(unused_mut)]
        let mut map = serde_json::Map::new();
        $(map.insert(String::from($key), serde_json::Value::from($value));)*
        serde_json::Value::Object(map)
    }};
}
pub(crate) use object;

/// `Some(string)` → JSON string, `None` → `null`.
pub(crate) fn opt_str(value: Option<impl Into<String>>) -> Value {
    value.map_or(Value::Null, |v| Value::String(v.into()))
}

/// Python truthiness, used where the original code filtered `if value`.
pub(crate) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

static BACKGROUND_URL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?im)background-image:\s*?url\([',"](.*)[',"]\)"#).expect("valid regex")
});

/// `re.search(r"<TAG.*?>(.*?)</TAG>", html, re.DOTALL)` without a regex
/// engine: the leftmost-first match is always the first `<TAG`, the first `>`
/// after it and the first `</TAG>` after that (or no match at all).
fn first_inner(html: &str, tag: &str) -> Option<String> {
    let open = format!("<{tag}");
    let close = format!("</{tag}>");
    let start = html.find(&open)? + open.len();
    let content = start + html[start..].find('>')? + 1;
    let end = content + html[content..].find(&close)?;
    Some(html[content..end].to_owned())
}

/// Inner HTML of the first `<tag>` in the node's serialised HTML.
pub(crate) fn inner_html(
    doc: &Document,
    node: Option<ElementRef<'_>>,
    tag: &str,
) -> Option<String> {
    first_inner(&doc.outer_html(node?), tag)
}

/// URL from a `background-image:url('...')` declaration.
pub(crate) fn background_url(style: Option<&str>) -> Option<String> {
    let style = style.filter(|s| !s.is_empty())?;
    BACKGROUND_URL.captures(style).map(|c| c[1].to_owned())
}

fn hex(byte: u8) -> Option<u8> {
    (byte as char)
        .to_digit(16)
        .and_then(|d| u8::try_from(d).ok())
}

/// `urllib.parse.unquote(value.replace("+", " "))`.
fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => out.push(b' '),
            b'%' if i + 2 < bytes.len() => {
                if let (Some(high), Some(low)) = (hex(bytes[i + 1]), hex(bytes[i + 2])) {
                    out.push(high * 16 + low);
                    i += 3;
                    continue;
                }
                out.push(b'%');
            }
            byte => out.push(byte),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `urllib.parse.parse_qs(urlparse(url).query)`, first parameter only, with
/// digit-only values converted to integers.
pub(crate) fn first_query_param(url: Option<&str>) -> Map<String, Value> {
    let mut result = Map::new();
    let Some(url) = url.filter(|u| !u.is_empty()) else {
        return result;
    };
    let without_fragment = url.split('#').next().unwrap_or_default();
    let Some((_, query)) = without_fragment.split_once('?') else {
        return result;
    };
    for pair in query.split('&') {
        let Some((name, value)) = pair.split_once('=') else {
            continue;
        };
        if value.is_empty() {
            continue;
        }
        let value = percent_decode(value);
        let json = if !value.is_empty() && value.chars().all(|c| c.is_ascii_digit()) {
            value
                .parse::<u64>()
                .map_or(Value::String(value.clone()), Value::from)
        } else {
            Value::String(value)
        };
        result.insert(percent_decode(name), json);
        break;
    }
    result
}

/// `content` of the first `<meta>` whose `attribute` equals `value`.
pub(crate) fn meta_content<'a>(
    root: ElementRef<'a>,
    attribute: &str,
    value: &str,
) -> Option<&'a str> {
    select(root, css!("meta"))
        .into_iter()
        .find(|meta| attr(*meta, attribute) == Some(value))
        .and_then(|meta| attr(meta, "content"))
}

/// Channel counters (`{type: value}`).
pub(crate) fn counters(nodes: &[ElementRef<'_>]) -> Map<String, Value> {
    let mut result = Map::new();
    for node in nodes {
        if let (Some(name), Some(value)) = (
            first(*node, css!(".counter_type")),
            first(*node, css!(".counter_value")),
        ) {
            result.insert(text(name), Value::String(text(value)));
        }
    }
    result
}

/// Header labels such as `verified`.
pub(crate) fn header_labels(root: ElementRef<'_>) -> Vec<Value> {
    select(root, css!(".tgme_header_labels>i"))
        .into_iter()
        .filter_map(|label| attr(label, "class").filter(|c| !c.is_empty()))
        .map(|class| Value::String(class.split('-').next().unwrap_or_default().to_owned()))
        .collect()
}

/// Pagination cursors from `<link rel=prev|next>` or "load more" anchors.
pub(crate) fn offsets(node: Option<ElementRef<'_>>, more: bool) -> Map<String, Value> {
    let mut result = Map::new();
    let Some(node) = node else { return result };
    let links = if more {
        select(node, css!("a.tme_messages_more"))
    } else {
        select(node, css!("link"))
    };
    for link in links {
        let is_page_link = matches!(attr(link, "rel"), Some("prev" | "next"));
        let is_more_link =
            attr(link, "data-before").is_some() || attr(link, "data-after").is_some();
        if is_page_link || is_more_link {
            for (key, value) in first_query_param(attr(link, "href")) {
                result.insert(key, value);
            }
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_params() {
        let get = |url| Value::Object(first_query_param(Some(url)));
        assert_eq!(get("/s/durov?before=42"), object! {"before" => 42});
        assert_eq!(get("/s/durov?q=a%20b&x=1"), object! {"q" => "a b"});
        assert_eq!(get("/s/durov?empty=&after=7#frag"), object! {"after" => 7});
        assert_eq!(get("/s/durov"), object! {});
    }

    #[test]
    fn first_inner_matches_the_lazy_regex() {
        let regex = |html: &str| {
            Regex::new(r"(?s)<div.*?>(.*?)</div>")
                .unwrap()
                .captures(html)
                .map(|c| c[1].to_owned())
        };
        for html in [
            "<div class=\"a\">x<div>y</div>z</div>",
            "<p>no</p>",
            "<div>unclosed",
            "<div a=\"1\"\n b>multi\nline</div>",
            "<divider>x</div>",
            "<div></div>",
            "text <div>a</div><div>b</div>",
        ] {
            assert_eq!(first_inner(html, "div"), regex(html), "{html}");
        }
    }

    #[test]
    fn background_urls() {
        assert_eq!(
            background_url(Some("width:1px;background-image:url('https://x/y.jpg')")),
            Some("https://x/y.jpg".into())
        );
        assert_eq!(background_url(Some("color:red")), None);
        assert_eq!(background_url(None), None);
    }

    #[test]
    fn truthiness_matches_python() {
        assert!(!truthy(&Value::Null));
        assert!(!truthy(&serde_json::json!([])));
        assert!(!truthy(&serde_json::json!({})));
        assert!(!truthy(&serde_json::json!("")));
        assert!(truthy(&serde_json::json!({"a": 1})));
    }
}
