//! Whole pages: channel feed, "load more" fragment, landing page.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value};

use super::dom::{Document, attr, css, first, select, text};
use super::post::parse_posts;
use super::{Parsed, counters, header_labels, inner_html, meta_content, object, offsets, opt_str};

static DESCRIPTION_DIV: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)\A<div.*?>(.*?)</div>").expect("valid regex"));
static LINK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<a.*?>(.*?)</a>").expect("valid regex"));

/// Parse `t.me/s/<channel>`; `None` when the page is not a public channel.
#[must_use]
pub fn parse_channel_page(html: &str, simplified: bool) -> Option<Parsed> {
    let doc = Document::parse(html);
    let root = doc.root();
    let username = first(root, css!(".tgme_channel_info_header_username>a"))?;
    let title = first(root, css!(".tgme_channel_info_header_title>span"))?;

    let description = meta_content(root, "property", "og:description").filter(|d| !d.is_empty());
    let channel = object! {
        "username" => text(username).chars().skip(1).collect::<String>(),
        "title" => object! {
            "string" => opt_str(meta_content(root, "property", "og:title")),
            "html" => opt_str(inner_html(&doc, Some(title), "span")),
        },
        "description" => description.map_or(Value::Null, |description| object! {
            "string" => description,
            "html" => inner_html(&doc, first(root, css!(".tgme_channel_info_description")), "div")
                .unwrap_or_default(),
        }),
        "avatar" => opt_str(meta_content(root, "property", "og:image")),
        "counters" => Value::Object(counters(&select(
            root,
            css!(".tgme_channel_info_counters>.tgme_channel_info_counter"),
        ))),
        "labels" => header_labels(root),
    };
    // Pagination is read before posts: post parsing unwraps formatting tags.
    let meta = object! {"offset" => Value::Object(offsets(doc.section("head"), false))};
    let mut skipped = Vec::new();
    let posts = doc
        .section("body")
        .map(|body| parse_posts(&doc, body, simplified, &mut skipped))
        .unwrap_or_default();
    Some(Parsed {
        value: object! {
            "channel" => channel,
            "content" => object! {"posts" => posts},
            "meta" => meta,
        },
        skipped,
    })
}

/// Parse the HTML fragment returned by the "load more" endpoint.
#[must_use]
pub fn parse_more_page(html: &str, simplified: bool) -> Parsed {
    let doc = Document::parse(html);
    let body = doc.section("body");
    let meta = object! {"offset" => Value::Object(offsets(body, true))};
    let mut skipped = Vec::new();
    let posts = body
        .map(|body| parse_posts(&doc, body, simplified, &mut skipped))
        .unwrap_or_default();
    Parsed {
        value: object! {"posts" => posts, "meta" => meta},
        skipped,
    }
}

fn preview_description(doc: &Document, node: Option<scraper::ElementRef<'_>>) -> Option<String> {
    let html = doc.outer_html(node?);
    let inner = DESCRIPTION_DIV.captures(&html)?.get(1)?.as_str().to_owned();
    if inner.is_empty() {
        return None;
    }
    let without_links = LINK.replace_all(&inner, "${1}").replace("<br>", "\n");
    (!without_links.is_empty()).then_some(without_links)
}

/// Parse the `t.me/<channel>` landing page; `None` when it is not a channel.
#[must_use]
pub fn parse_preview_page(html: &str) -> Option<Value> {
    let doc = Document::parse(html);
    let page = first(doc.root(), css!(".tgme_page"))?;
    let title = first(page, css!(".tgme_page_title>span"))?;
    let context_link = first(page, css!(".tgme_page_context_link"))?;
    if text(context_link) != "Preview channel" {
        return None;
    }

    let mut channel = Map::new();
    channel.insert("title".into(), Value::from(text(title)));
    channel.insert(
        "subscribers".into(),
        Value::from(
            first(page, css!(".tgme_page_extra"))
                .map(text)
                .unwrap_or_default(),
        ),
    );
    channel.insert(
        "is_verified".into(),
        Value::Bool(first(page, css!("i.verified-icon")).is_some()),
    );
    if let Some(description) =
        preview_description(&doc, first(page, css!(".tgme_page_description")))
    {
        channel.insert("description".into(), Value::from(description));
    }
    if let Some(avatar) = first(page, css!("img.tgme_page_photo_image")) {
        channel.insert("avatar".into(), opt_str(attr(avatar, "src")));
    }
    Some(object! {"channel" => Value::Object(channel)})
}
