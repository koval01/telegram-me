from fastapi import HTTPException

from app.utils.config import settings


def require_feature_enabled(enabled: bool, feature_name: str) -> None:
    """Raise standardized error when feature is disabled by configuration."""
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail=f"Feature '{feature_name}' is disabled by server configuration",
        )


def is_feed_enabled() -> bool:
    """Return whether feed endpoint is enabled."""
    return settings.ENABLE_FEED


def is_previews_enabled() -> bool:
    """Return whether preview endpoints are enabled."""
    return settings.ENABLE_PREVIEWS


def cache_mode() -> str:
    """Return configured cache mode."""
    return settings.CACHE_MODE


def parser_mode() -> str:
    """Return configured parser mode."""
    return settings.PARSER_MODE
