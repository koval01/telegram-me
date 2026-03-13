import re

MAX_POST_ID = 10 ** 7
MAX_CHANNELS_IN_PREVIEW = 100
CHANNEL_MIN_LENGTH = 4
CHANNEL_MAX_LENGTH = 32
CHANNEL_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_]{3,31}$"
CHANNEL_REGEX = re.compile(CHANNEL_PATTERN)


def normalize_channel(channel: str) -> str:
    """Normalize channel name before validation."""
    return channel.strip()


def is_valid_channel(channel: str) -> bool:
    """Validate channel name against canonical Telegram username pattern."""
    return bool(CHANNEL_REGEX.fullmatch(normalize_channel(channel)))


def validate_channel_or_raise(channel: str) -> str:
    """Return normalized channel name or raise ValueError."""
    normalized_channel = normalize_channel(channel)
    if not is_valid_channel(normalized_channel):
        raise ValueError("Invalid Telegram username format")
    return normalized_channel
