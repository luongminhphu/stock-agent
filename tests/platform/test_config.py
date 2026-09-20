import os
from unittest.mock import patch

from src.platform.config import Settings

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/test",
    "PERPLEXITY_API_KEY": "pplx-test",
    "DISCORD_TOKEN": "test-token",
}


def test_settings_loads_from_env() -> None:
    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        s = Settings()
        assert s.database_url == MINIMAL_ENV["DATABASE_URL"]
        assert s.perplexity_api_key == MINIMAL_ENV["PERPLEXITY_API_KEY"]
        assert s.environment == "development"  # default


def test_is_production_flag() -> None:
    with patch.dict(os.environ, {**MINIMAL_ENV, "ENVIRONMENT": "production"}, clear=True):
        s = Settings()
        assert s.is_production is True
        assert s.is_development is False


def test_is_development_flag() -> None:
    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        s = Settings()
        assert s.is_development is True
        assert s.is_production is False


def test_empty_env_falls_back_to_defaults() -> None:
    """Không còn field bắt buộc: thiếu env → default rỗng/SQLite, không raise.

    Guard runtime cho token/API key nằm ở bootstrap / bot startup, không ở Settings.
    """
    with patch.dict(os.environ, {}, clear=True):
        s = Settings(_env_file=None)
        assert s.discord_token == ""
        assert s.perplexity_api_key == ""
        assert s.database_url.startswith("sqlite+aiosqlite")
        assert s.is_development is True
