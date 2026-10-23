"""Validation rules for user-supplied Telegram identifiers."""

import re

MAX_POST_ID = 10**7
MAX_CHANNELS_PER_REQUEST = 100
CHANNEL_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_]{3,31}$"
_CHANNEL_REGEX = re.compile(CHANNEL_PATTERN)


def normalize_channel(channel: str) -> str:
    """Strip surrounding whitespace from a channel username."""
    return channel.strip()


def is_valid_channel(channel: str) -> bool:
    """Whether `channel` is a syntactically valid public Telegram username."""
    return _CHANNEL_REGEX.fullmatch(normalize_channel(channel)) is not None


def validate_channel_or_raise(channel: str) -> str:
    """Return the normalized username or raise `ValueError`."""
    normalized = normalize_channel(channel)
    if not is_valid_channel(normalized):
        msg = "Invalid Telegram username format"
        raise ValueError(msg)
    return normalized
