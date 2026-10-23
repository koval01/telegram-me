"""Media attachments (photos, videos, voice, round videos, stickers) of a post."""

from collections.abc import Callable

from selectolax.lexbor import LexborNode

from telegram_me.telegram.parsing.html import JSONDict, background_url

_CONTENT_TYPES = {
    "link_preview_image": "image",
    "tgme_widget_message_photo_wrap": "image",
    "tgme_widget_message_video_player": "video",
    "tgme_widget_message_voice_player": "voice",
    "tgme_widget_message_roundvideo_player": "roundvideo",
    "tgme_widget_message_sticker_wrap": "sticker",
}
_MEDIA_SELECTOR = ",".join(f".{class_name}" for class_name in _CONTENT_TYPES)
# (container selector, source element selector, attribute holding the URL)
_STICKER_SOURCES = (
    ("picture.tgme_widget_message_tgsticker", "source", "srcset"),
    ("i.tgme_widget_message_sticker", "i.tgme_widget_message_sticker", "data-webp"),
    ("div.tgme_widget_message_videosticker", "video.js-videosticker_video", "src"),
)


def duration_seconds(duration: str | None) -> int | None:
    """Convert `MM:SS` or `HH:MM:SS` to seconds."""
    if not duration:
        return None
    try:
        parts = [int(part) for part in duration.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    return None


def _duration(text: str | None) -> JSONDict | None:
    if not text:
        return None
    return {"formatted": text, "raw": duration_seconds(text)}


def _image(node: LexborNode) -> JSONDict | None:
    style = node.attributes.get("style")
    if not style:
        return None
    return {"url": background_url(style), "type": "image"}


def _video(node: LexborNode) -> JSONDict:
    thumb = node.css_first(".tgme_widget_message_video_thumb")
    duration = node.css_first("time.message_video_duration")
    video = node.css_first("video.tgme_widget_message_video")

    source = video.attributes.get("src") if video is not None else None
    media: JSONDict = {
        "url": source or None,
        "thumb": background_url(thumb.attributes.get("style")) if thumb is not None else None,
        "type": "video",
    }
    if not source:
        media["available"] = False
    if duration is not None:
        media["duration"] = _duration(duration.text())
    else:
        media["type"] = "gif"
    return media


def _voice(node: LexborNode) -> JSONDict | None:
    audio = node.css_first(".tgme_widget_message_voice")
    if audio is None:
        return None
    duration = node.css_first("time.tgme_widget_message_voice_duration")
    return {
        "url": audio.attributes.get("src"),
        "waves": audio.attributes.get("data-waveform"),
        "duration": _duration(duration.text() if duration is not None else None),
        "type": "voice",
    }


def _roundvideo(node: LexborNode) -> JSONDict | None:
    video = node.css_first("video.tgme_widget_message_roundvideo")
    if video is None:
        return None
    thumb = node.css_first(".tgme_widget_message_roundvideo_thumb")
    duration = node.css_first("time.tgme_widget_message_roundvideo_duration")
    return {
        "url": video.attributes.get("src"),
        "thumb": background_url(thumb.attributes.get("style")) if thumb is not None else None,
        "duration": _duration(duration.text() if duration is not None else None),
        "type": "roundvideo",
    }


def _sticker(node: LexborNode) -> JSONDict | None:
    for container_selector, source_selector, attribute in _STICKER_SOURCES:
        container = node.css_first(container_selector)
        if container is None:
            continue
        source = container.css_first(source_selector)
        if source is None:
            return None
        media: JSONDict = {"url": source.attributes.get(attribute), "type": "sticker"}
        thumb = source.css_first("img")
        if thumb is not None:
            media["thumb"] = thumb.attributes.get("src")
        return media
    return None


_EXTRACTORS: dict[str, Callable[[LexborNode], JSONDict | None]] = {
    "image": _image,
    "video": _video,
    "voice": _voice,
    "roundvideo": _roundvideo,
    "sticker": _sticker,
}


def extract_media(bubble: LexborNode) -> list[JSONDict]:
    """All media attachments found in a message bubble, in document order."""
    media: list[JSONDict] = []
    for node in bubble.css(_MEDIA_SELECTOR):
        classes = (node.attributes.get("class") or "").split()
        content_type = _CONTENT_TYPES.get(classes[0]) if classes else None
        if content_type is None:
            continue
        item = _EXTRACTORS[content_type](node)
        if item:
            media.append(item)
    return media
