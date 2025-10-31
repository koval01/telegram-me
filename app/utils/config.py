from pydantic_settings import BaseSettings


class Settings(BaseSettings):  # pylint: disable=R0903
    """
    This class is used to load environment variables
    from the specified `.env.local` file.
    """

    DISABLE_DOCS: int = 0
    VERSION: str = "1.7"

    class Config:  # pylint: disable=R0903
        """
        Configuration class that specifies the location of
        the .env file to be used for loading environment variables.
        """

        env_file = "./.env.local"


# Load the settings from the .env file
settings = Settings()
