"""Settings, read from the environment or a local .env file.

Everything configurable lives here so nothing else in the codebase reads os.environ
directly. get_settings() is cached; tests clear the cache via the reset_settings
fixture in tests/conftest.py.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    # Shared secret n8n must present on /ingest.
    ingest_token: str = ""

    # Anthropic
    anthropic_api_key: str = ""
    classify_model: str = "claude-haiku-4-5-20251001"
    extract_model: str = "claude-sonnet-5"

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Monitoring
    sentry_dsn: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
