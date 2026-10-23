//! Media attachments (photos, videos, voice, round videos, stickers).

use scraper::ElementRef;
use serde_json::Value;

use super::dom::{attr, css, first, select, text};
use super::{background_url, object, opt_str};

/// `MM:SS` or `HH:MM:SS` in seconds.
#[must_use]
pub fn duration_seconds(duration: Option<&str>) -> Option<i64> {
    let duration = duration.filter(|d| !d.is_empty())?;
    let parts: Option<Vec<i64>> = duration.split(':').map(|p| p.trim().parse().ok()).collect();
    match parts?.as_slice() {
        [minutes, seconds] => Some(minutes * 60 + seconds),
        [hours, minutes, seconds] => Some(hours * 3600 + minutes * 60 + seconds),
        _ => None,
    }
}

fn duration(text: Option<String>) -> Value {
    match text.filter(|t| !t.is_empty()) {
        Some(text) => {
            let raw = duration_seconds(Some(&text)).map_or(Value::Null, Value::from);
            object! {"formatted" => text, "raw" => raw}
        }
        None => Value::Null,
    }
}

fn image(node: ElementRef<'_>) -> Option<Value> {
    let style = attr(node, "style").filter(|s| !s.is_empty())?;
    Some(object! {"url" => opt_str(background_url(Some(style))), "type" => "image"})
}

fn video(node: ElementRef<'_>) -> Value {
    let thumb = first(node, css!(".tgme_widget_message_video_thumb"));
    let length = first(node, css!("time.message_video_duration"));
    let player = first(node, css!("video.tgme_widget_message_video"));
    let source = player
        .and_then(|p| attr(p, "src"))
        .filter(|s| !s.is_empty());

    let mut media = object! {
        "url" => opt_str(source),
        "thumb" => opt_str(thumb.and_then(|t| background_url(attr(t, "style")))),
        "type" => "video",
    };
    let map = media.as_object_mut().expect("object");
    if source.is_none() {
        map.insert("available".into(), Value::Bool(false));
    }
    if let Some(length) = length {
        map.insert("duration".into(), duration(Some(text(length))));
    } else {
        map.insert("type".into(), Value::from("gif"));
    }
    media
}

fn voice(node: ElementRef<'_>) -> Option<Value> {
    let audio = first(node, css!(".tgme_widget_message_voice"))?;
    let length = first(node, css!("time.tgme_widget_message_voice_duration"));
    Some(object! {
        "url" => opt_str(attr(audio, "src")),
        "waves" => opt_str(attr(audio, "data-waveform")),
        "duration" => duration(length.map(text)),
        "type" => "voice",
    })
}

fn roundvideo(node: ElementRef<'_>) -> Option<Value> {
    let video = first(node, css!("video.tgme_widget_message_roundvideo"))?;
    let thumb = first(node, css!(".tgme_widget_message_roundvideo_thumb"));
    let length = first(node, css!("time.tgme_widget_message_roundvideo_duration"));
    Some(object! {
        "url" => opt_str(attr(video, "src")),
        "thumb" => opt_str(thumb.and_then(|t| attr(t, "style").filter(|s| !s.is_empty())).and_then(|s| background_url(Some(s)))),
        "duration" => duration(length.map(text)),
        "type" => "roundvideo",
    })
}

fn sticker(node: ElementRef<'_>) -> Option<Value> {
    let variants: [(&scraper::Selector, &scraper::Selector, &str); 3] = [
        (
            css!("picture.tgme_widget_message_tgsticker"),
            css!("source"),
            "srcset",
        ),
        (
            css!("i.tgme_widget_message_sticker"),
            css!("i.tgme_widget_message_sticker"),
            "data-webp",
        ),
        (
            css!("div.tgme_widget_message_videosticker"),
            css!("video.js-videosticker_video"),
            "src",
        ),
    ];
    for (container_selector, source_selector, attribute) in variants {
        let Some(container) = first(node, container_selector) else {
            continue;
        };
        let source = first(container, source_selector)?;
        let mut media = object! {"url" => opt_str(attr(source, attribute)), "type" => "sticker"};
        if let Some(thumb) = first(source, css!("img")) {
            media
                .as_object_mut()
                .expect("object")
                .insert("thumb".into(), opt_str(attr(thumb, "src")));
        }
        return Some(media);
    }
    None
}

/// All media attachments found in a message bubble, in document order.
#[must_use]
pub fn extract_media(bubble: ElementRef<'_>) -> Vec<Value> {
    let selector = css!(
        ".link_preview_image,.tgme_widget_message_photo_wrap,.tgme_widget_message_video_player,\
         .tgme_widget_message_voice_player,.tgme_widget_message_roundvideo_player,\
         .tgme_widget_message_sticker_wrap"
    );
    let mut media = Vec::new();
    for node in select(bubble, selector) {
        let class = attr(node, "class").unwrap_or_default();
        let item = match class.split_whitespace().next() {
            Some("link_preview_image" | "tgme_widget_message_photo_wrap") => image(node),
            Some("tgme_widget_message_video_player") => Some(video(node)),
            Some("tgme_widget_message_voice_player") => voice(node),
            Some("tgme_widget_message_roundvideo_player") => roundvideo(node),
            Some("tgme_widget_message_sticker_wrap") => sticker(node),
            _ => None,
        };
        if let Some(item) = item.filter(|v| v.as_object().is_some_and(|o| !o.is_empty())) {
            media.push(item);
        }
    }
    media
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parse::dom::Document;
    use serde_json::json;

    #[test]
    fn durations() {
        assert_eq!(duration_seconds(Some("0:05")), Some(5));
        assert_eq!(duration_seconds(Some("2:30")), Some(150));
        assert_eq!(duration_seconds(Some("1:02:03")), Some(3723));
        assert_eq!(duration_seconds(Some("x")), None);
        assert_eq!(duration_seconds(Some("")), None);
    }

    #[test]
    fn sticker_voice_and_unavailable_video() {
        let doc = Document::parse(
            r#"<div class="bubble">
              <div class="tgme_widget_message_sticker_wrap">
                <i class="tgme_widget_message_sticker" data-webp="https://cdn.example/s.webp"></i>
              </div>
              <a class="tgme_widget_message_voice_player">
                <audio class="tgme_widget_message_voice" src="https://cdn.example/v.ogg" data-waveform="abc"></audio>
                <time class="tgme_widget_message_voice_duration">0:07</time>
              </a>
              <div class="tgme_widget_message_video_player"><video class="tgme_widget_message_video"></video></div>
            </div>"#,
        );
        let bubble = first(doc.root(), css!(".bubble")).unwrap();
        assert_eq!(
            extract_media(bubble),
            vec![
                json!({"url": "https://cdn.example/s.webp", "type": "sticker"}),
                json!({"url": "https://cdn.example/v.ogg", "waves": "abc",
                       "duration": {"formatted": "0:07", "raw": 7}, "type": "voice"}),
                json!({"url": null, "thumb": null, "type": "gif", "available": false}),
            ]
        );
    }
}
