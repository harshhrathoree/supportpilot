"""Central place for configuration.

Values come from environment variables, or from a local `.env` file.
Every other module imports `settings` from here instead of reading os.environ directly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads the .env file in the project root; unknown keys in it are ignored.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    database_url: str = ""


settings = Settings()
