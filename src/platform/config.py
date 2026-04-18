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

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


settings = Settings()
