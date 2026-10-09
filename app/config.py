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

    # Any provider speaking the OpenAI chat-completions shape — Groq, Gemini's compatible
    # endpoint, OpenRouter, a local server. Consulted only when anthropic_api_key is
    # empty, so switching to Anthropic is filling in one line and switching back is
    # clearing it. Model names are separate because no two providers call them the same.
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_classify_model: str = ""
    llm_extract_model: str = ""

    # Supabase. supabase_secret_key is the sb_secret_... key, formerly service_role:
    # it bypasses row-level security and must never reach the frontend.
    supabase_url: str = ""
    supabase_secret_key: str = ""

    # Where the dashboard is served from, comma-separated. Localhost is always allowed;
    # this adds the deployed origin. Needed because the browser calls /matches directly.
    dashboard_origin: str = ""

    # Vercel mints a new hostname for every deployment
    # (kaptainx-5lkf2wh1h-kapt-ainx.vercel.app), so an exact list goes stale the next
    # time anybody deploys and the dashboard dies with a CORS error that looks like a
    # server fault. This matches a family of them instead.
    #
    # Scope it to YOUR project. `.*\.vercel\.app` would let any page anyone deploys on
    # Vercel read this client's board with the viewer's own session.
    dashboard_origin_regex: str = ""

    # Monitoring
    sentry_dsn: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
