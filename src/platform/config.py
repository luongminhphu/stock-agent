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
    # Set DB_ECHO=true in .env to enable SQL query tracing.
    # Intentionally separate from is_development — avoids log noise in dev by default.
    db_echo: bool = False

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

    # Alert channel — Wave 4 proactive alerts (RecommendationListener)
    # Bỏ trống = dùng chung morning_channel_id
    discord_alert_channel_id: str = ""

    # Thesis Drift Detector
    thesis_drift_threshold_pct: float = 5.0   # Trigger review khi |drift| >= threshold
    thesis_drift_cooldown_hours: float = 4.0  # Không re-trigger trong N giờ sau lần review gần nhất

    # Alert auto-reactivation cooldown
    # Alerts with auto_reactivate=True will be reset to ACTIVE after this many hours
    # following their triggered_at timestamp. Set to 0 to disable.
    alert_reactivate_cooldown_hours: int = 4

    # ------------------------------------------------------------------
    # Investor Static Profile — Wave 1 Blueprint V2
    # Edit these in .env when your investment style changes.
    # Consumed by InvestorProfileService.StaticProfile.from_settings()
    # and injected into every AI agent call via ContextBuilder (Wave 2).
    # ------------------------------------------------------------------

    investor_risk_appetite: str = (
        "medium — max drawdown 15%, position size ≤20%, không dùng margin"
    )
    investor_thesis_style: str = (
        "fundamental + macro top-down, hold 2-6 tháng, tập trung chu kỳ ngành"
    )
    investor_trading_horizon: str = "positional — không day trade, không T+"
    investor_preferred_sectors: str = (
        "tài chính (VCB, BID, CTG, TCB, MBB), "
        "nguyên vật liệu và sắt thép (HPG, HSG, NKG, TLH), "
        "năng lượng (PVD, BSR, GAS, PLC)"
    )
    investor_avoid: str = (
        "cổ phiếu penny dưới 5000đ, "
        "nhóm bất động sản thanh khoản thấp, "
        "IPO năm đầu, "
        "T+ dưới 10 ngàn đơn vị/phiên"
    )

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
    def alert_channel_id(self) -> str:
        """Resolve alert channel: discord_alert_channel_id → morning_channel_id.

        RecommendationListener dùng property này để không cần hardcode fallback chain.
        Nếu muốn tách channel riêng cho AI alert, set DISCORD_ALERT_CHANNEL_ID trong .env.
        """
        return self.discord_alert_channel_id or self.morning_channel_id

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
