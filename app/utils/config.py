from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):  # pylint: disable=R0903
    """
    This class is used to load environment variables
    from the specified `.env.local` file.
    """

    DISABLE_DOCS: int = 0
    VERSION: str = "1.7"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Feature flags
    ENABLE_FEED: bool = True
    ENABLE_PREVIEWS: bool = True
    ENABLE_RATE_LIMIT: bool = False
    RATE_LIMIT_RPM: int = 120

    # Performance modes
    CACHE_MODE: Literal["off", "normal", "aggressive"] = "normal"
    PARSER_MODE: Literal["full", "simplified"] = "full"

    model_config = SettingsConfigDict(env_file="./.env.local")


# Load the settings from the .env file
settings = Settings()
