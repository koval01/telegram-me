"""TelegramMe: REST API for public Telegram channels built on t.me web previews."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("telegram-me")
except PackageNotFoundError:  # pragma: no cover - running from a raw checkout
    __version__ = "0.0.0"

__all__ = ["__version__"]
