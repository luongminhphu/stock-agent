"""Unit tests for adapter factory."""

from __future__ import annotations

import os
import pytest


def test_factory_returns_mock_in_test_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("MOCK_MARKET", "false")
    # Re-import with patched env
    from importlib import reload
    import src.platform.config as cfg_mod

    reload(cfg_mod)
    import src.market.adapters.factory as factory_mod

    reload(factory_mod)

    from src.market.adapters.mock import MockAdapter

    adapter = factory_mod.build_adapter()
    assert isinstance(adapter, MockAdapter)


def test_factory_returns_mock_when_mock_market_true(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MOCK_MARKET", "true")
    from importlib import reload
    import src.platform.config as cfg_mod

    reload(cfg_mod)
    import src.market.adapters.factory as factory_mod

    reload(factory_mod)

    from src.market.adapters.mock import MockAdapter

    adapter = factory_mod.build_adapter()
    assert isinstance(adapter, MockAdapter)
