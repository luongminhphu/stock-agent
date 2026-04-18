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

    # Perplexity AI
    perplexity_api_key: str = ""

    # CORS (comma-separated origins)
    cors_origins_raw: str = "http://localhost:3000,http://localhost:8080"

    # Feature flags
    mock_market: bool = False  # Force MockAdapter regardless of environment

    # Briefing scheduler
    # Channel IDs where scheduled briefs are sent (Discord channel snowflake IDs).
    # Leave empty to disable that scheduled brief.
    morning_channel_id: str = ""  # e.g. "123456789012345678"
    eod_channel_id: str = ""      # e.g. "123456789012345679"

    # Service-account user_id whose watchlist drives scheduled briefs.
    # Must match a Discord user snowflake that has a populated watchlist.
    scheduler_user_id: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def briefing_scheduler_enabled(self) -> bool:
        """True only when all three scheduler fields are configured."""
        return bool(self.morning_channel_id or self.eod_channel_id) and bool(self.scheduler_user_id)


settings = Settings()
