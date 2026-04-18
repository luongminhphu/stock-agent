from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Platform
    environment: str = "development"
    log_level: str = "INFO"

    # Database
    database_url: str

    # AI
    perplexity_api_key: str

    # Bot
    discord_token: str
    discord_guild_id: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings instance. Use this everywhere — do not instantiate Settings() directly."""
    return Settings()


# Module-level convenience alias
settings = get_settings()
