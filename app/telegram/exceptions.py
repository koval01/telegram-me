class TelegramError(Exception):
    """Base exception for Telegram integration errors."""


class TelegramValidationError(TelegramError):
    """Raised when input parameters are invalid."""


class TelegramUpstreamError(TelegramError):
    """Raised when upstream request fails."""


class TelegramNotFoundError(TelegramError):
    """Raised when requested Telegram resource does not exist."""


class TelegramParseError(TelegramError):
    """Raised when upstream payload cannot be parsed."""
