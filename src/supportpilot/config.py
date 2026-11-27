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

    # Model names live here, not in the code, so switching models is a one-line change in .env.
    llm_model: str = "gemini-3.5-flash-lite"
    # Used if llm_model keeps failing after retries (e.g. Google returns 503 "high demand").
    llm_fallback_model: str = "gemini-3.1-flash-lite"
    # The free tier allows 15 requests/minute per model. Stay a little below it.
    # Set to 0 to disable limiting (for example on a paid plan).
    llm_max_requests_per_minute: int = 12

    # Embeddings turn text into a list of numbers that captures its meaning (used for policy search).
    # 768 numbers per text. The database column size must match, so changing it means re-embedding.
    embedding_model: str = "gemini-embedding-001"
    embedding_dims: int = 768


settings = Settings()
