"""Platform configuration.

Owner: platform segment.
Single source of truth for all settings. All segments import from here.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Runtime
    environment: str = "development"  # development | test | production

    # Database
    database_url: str = "sqlite+aiosqlite:///./stock_agent.db"

    # Discord bot
    discord_token: str = ""

    # Owner User ID
    owner_user_id: str = ""

    # Perplexity AI
    perplexity_api_key: str = ""

    # CORS (comma-separated origins)
    cors_origins_raw: str = "http://localhost:3000,http://localhost:8080"

    # Feature flags
    mock_market: bool = False  # Force MockAdapter regardless of environment

    # Briefing scheduler
    morning_channel_id: str = ""
    eod_channel_id: str = ""
    scheduler_user_id: str = ""

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def briefing_scheduler_enabled(self) -> bool:
        """True only when all three scheduler fields are configured."""
        return bool(self.morning_channel_id or self.eod_channel_id) and bool(self.scheduler_user_id)

    @property
    def is_single_user(self) -> bool:
        """True khi app chạy single-owner mode (no multi-user auth)."""
        return bool(self.owner_user_id)

# Singleton — imported directly by most modules
settings = Settings()


def get_settings() -> Settings:
    """Factory returning the singleton Settings instance.

    Useful for FastAPI Depends() and testing overrides:
        app.dependency_overrides[get_settings] = lambda: test_settings
    """
    return settings
