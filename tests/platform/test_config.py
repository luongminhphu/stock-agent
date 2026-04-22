import os
from unittest.mock import patch

import pytest

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


def test_missing_required_fields_raises() -> None:
    with patch.dict(os.environ, {}, clear=True), pytest.raises(Exception):
        Settings()
