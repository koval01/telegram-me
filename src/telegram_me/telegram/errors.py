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


class OverloadedError(TelegramUpstreamError):
    """Too much upstream work is queued; the request is shed instead of waiting."""

    def __init__(self, message: str = "Server is overloaded, retry later", retry_after: int = 1):
        super().__init__(message)
        self.retry_after = retry_after
