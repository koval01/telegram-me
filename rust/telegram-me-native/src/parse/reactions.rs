//! Reactions (emoji, custom emoji, Telegram Stars) attached to a post.

use std::sync::LazyLock;

use regex::Regex;
use scraper::ElementRef;
use scraper::node::Node;
use serde_json::{Map, Value};

use super::dom::{attr, css, first, select, text};
use super::opt_str;

static EMOJI_URL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"url\(['"]?(//[^'")]+)['"]?\)"#).expect("valid regex"));

fn emoji_image(node: ElementRef<'_>) -> Option<String> {
    let style = attr(node, "style").unwrap_or_default();
    if !style.contains("background-image") {
        return None;
    }
    let url = EMOJI_URL.captures(style)?.get(1)?.as_str();
    let host = url
        .get(2..)
        .unwrap_or_default()
        .split('/')
        .next()
        .unwrap_or_default();
    (!host.is_empty()).then(|| format!("https:{url}"))
}

/// Text of the node's last child, which may be a text node.
fn last_child_text(node: ElementRef<'_>) -> Option<String> {
    let last = node.last_child()?;
    match last.value() {
        Node::Text(t) => Some(t.to_string()),
        Node::Element(_) => ElementRef::wrap(last).map(text),
        _ => Some(String::new()),
    }
}

fn reaction(node: ElementRef<'_>) -> Option<Value> {
    let mut reaction = Map::new();
    reaction.insert("count".into(), Value::from(last_child_text(node)?));

    if attr(node, "class")
        .unwrap_or_default()
        .contains("tgme_reaction_paid")
    {
        reaction.insert("type".into(), Value::from("telegram_stars"));
        reaction.insert("emoji".into(), Value::from("⭐"));
        return Some(Value::Object(reaction));
    }

    if let Some(emoji) = first(node, css!("i.emoji")) {
        if let Some(symbol) = first(emoji, css!("b")) {
            reaction.insert("emoji".into(), Value::from(text(symbol)));
        }
        let image = emoji_image(emoji)?;
        reaction.insert("emoji_image".into(), Value::from(image));
        reaction.insert("type".into(), Value::from("emoji"));
        return Some(Value::Object(reaction));
    }

    if let Some(custom) = first(node, css!("tg-emoji")) {
        reaction.insert("type".into(), Value::from("custom_emoji"));
        reaction.insert("emoji_id".into(), opt_str(attr(custom, "emoji-id")));
        return Some(Value::Object(reaction));
    }
    None
}

/// Reactions of a message, or `null` when it has none.
#[must_use]
pub fn parse_reactions(message: ElementRef<'_>) -> Value {
    let Some(container) = first(message, css!(".tgme_widget_message_reactions")) else {
        return Value::Null;
    };
    let reactions: Vec<Value> = select(container, css!(".tgme_reaction"))
        .into_iter()
        .filter_map(reaction)
        .collect();
    if reactions.is_empty() {
        Value::Null
    } else {
        Value::Array(reactions)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parse::dom::Document;
    use serde_json::json;

    #[test]
    fn reaction_kinds() {
        let doc = Document::parse(
            r#"<div class="m"><div class="tgme_widget_message_reactions">
              <span class="tgme_reaction tgme_reaction_paid"><i></i>50</span>
              <span class="tgme_reaction"><i class="emoji" style="background-image:url('//telegram.org/e.png')"><b>👍</b></i>1.2K</span>
              <span class="tgme_reaction"><tg-emoji emoji-id="42"></tg-emoji>7</span>
              <span class="tgme_reaction"><i class="emoji" style="color:red"><b>x</b></i>3</span>
            </div></div>"#,
        );
        let message = first(doc.root(), css!(".m")).unwrap();
        assert_eq!(
            parse_reactions(message),
            json!([
                {"count": "50", "type": "telegram_stars", "emoji": "⭐"},
                {"count": "1.2K", "emoji": "👍", "emoji_image": "https://telegram.org/e.png", "type": "emoji"},
                {"count": "7", "type": "custom_emoji", "emoji_id": "42"},
            ])
        );
    }

    #[test]
    fn no_reactions_is_null() {
        let doc = Document::parse("<div class=\"m\"></div>");
        assert_eq!(
            parse_reactions(first(doc.root(), css!(".m")).unwrap()),
            Value::Null
        );
    }
}
