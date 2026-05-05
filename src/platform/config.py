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
    discord_guild_id: str = ""  # Set để sync slash commands tức thì (guild-specific)
    # Bỏ trống = global sync (~1 giờ)

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

    # Thesis Drift Detector
    thesis_drift_threshold_pct: float = 5.0   # Trigger review khi |drift| >= threshold
    thesis_drift_cooldown_hours: float = 4.0  # Không re-trigger trong N giờ sau lần review gần nhất

    # ------------------------------------------------------------------
    # Investor Static Profile — Wave 1 Blueprint V2
    # Edit these in .env when your investment style changes.
    # Consumed by InvestorProfileService.StaticProfile.from_settings()
    # and injected into every AI agent call via ContextBuilder (Wave 2).
    # ------------------------------------------------------------------

    investor_risk_appetite: str = "medium — max drawdown 10%, position size ≤15%"
    investor_thesis_style: str = "fundamental, hold 3-6 tháng"
    investor_trading_horizon: str = "swing to positional — không day trade"
    investor_preferred_sectors: str = "banking, consumer staples, tech"
    investor_avoid: str = "speculative penny stocks, T+ illiquid"

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
