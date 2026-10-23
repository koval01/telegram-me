"""Errors raised while talking to t.me or interpreting its pages."""


class TelegramError(Exception):
    """Base class for Telegram integration errors."""


class TelegramValidationError(TelegramError):
    """Input parameters can not be sent to t.me."""


class TelegramUpstreamError(TelegramError):
    """t.me could not be reached or answered with an error status."""


class TelegramNotFoundError(TelegramError):
    """The requested channel or post does not exist (or is not public)."""


class TelegramParseError(TelegramError):
    """The t.me response did not have the expected shape."""
