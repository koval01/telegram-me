from datetime import datetime, timezone
from typing import Optional


def parse_content_type_single(post_content: dict) -> dict:
    """Parse supported content types for one post."""
    processed = {"text": "", "photos": 0, "videos": 0, "gifs": 0, "poll": 0}

    text_data = post_content.get("text")
    if text_data:
        processed["text"] = (
            str(text_data.get("string", "")) if isinstance(text_data, dict) else str(text_data)
        )

    media_list = post_content.get("media", [])
    for media in media_list:
        media_type = media.get("type", "")
        if media_type == "image":
            processed["photos"] += 1
        elif media_type == "video":
            processed["videos"] += 1
        elif media_type == "sticker":
            processed["gifs"] += 1

    return processed


def parse_reactions_single(reacts_data: Optional[list], parse_numeric_value_fn) -> dict:
    """Parse reaction payload into emoji->count mapping."""
    if not reacts_data:
        return {}

    reactions = {}
    for react in reacts_data:
        emoji = react.get("emoji", "")
        if emoji:
            count_str = str(react.get("count", "0"))
            count = parse_numeric_value_fn(count_str, 0)
            if count > 0:
                reactions[emoji] = count

    return reactions


def parse_datetime_fast(date_str: str) -> datetime:
    """Parse datetime from Telegram payload."""
    try:
        if "Z" in date_str:
            date_str = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(date_str)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def parse_numeric_value(value_str: Optional[str], default: int = 0) -> int:
    """Parse compact numeric strings like 1.2k, 3m."""
    if not value_str:
        return default

    cleaned = value_str.replace(" ", "").lower()

    if cleaned.isdigit():
        return int(cleaned)

    try:
        if "k" in cleaned:
            return int(float(cleaned.replace("k", "")) * 1000)
        if "m" in cleaned:
            return int(float(cleaned.replace("m", "")) * 1000000)
        return int(cleaned)
    except (ValueError, TypeError):
        return default
