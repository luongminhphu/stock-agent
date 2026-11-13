"""Unit tests for ChainedAdapter."""

from __future__ import annotations

import pytest

from src.market.adapters.chained import ChainedAdapter
from src.market.adapters.mock import MockAdapter


async def test_uses_primary_when_ok() -> None:
    primary = MockAdapter()
    secondary = MockAdapter(fail_tickers={"HPG"})  # secondary would fail
    chain = ChainedAdapter(primary=primary, secondary=secondary)
    quote = await chain.fetch_quote("HPG")
    assert quote.ticker == "HPG"  # primary succeeded


async def test_falls_back_to_secondary_when_primary_fails() -> None:
    primary = MockAdapter(fail_tickers={"HPG"})
    secondary = MockAdapter()
    chain = ChainedAdapter(primary=primary, secondary=secondary)
    quote = await chain.fetch_quote("HPG")
    assert quote.ticker == "HPG"  # secondary succeeded


async def test_raises_when_both_fail() -> None:
    primary = MockAdapter(fail_tickers={"ERR"})
    secondary = MockAdapter(fail_tickers={"ERR"})
    chain = ChainedAdapter(primary=primary, secondary=secondary)
    with pytest.raises(ValueError):
        await chain.fetch_quote("ERR")


async def test_bulk_primary_ok() -> None:
    primary = MockAdapter()
    secondary = MockAdapter(fail_tickers={"HPG", "VNM"})
    chain = ChainedAdapter(primary=primary, secondary=secondary)
    quotes = await chain.fetch_bulk_quotes(["HPG", "VNM"])
    assert len(quotes) == 2


async def test_bulk_falls_back_to_secondary() -> None:
    primary = MockAdapter(fail_tickers={"HPG"})  # fails on any call
    secondary = MockAdapter()
    chain = ChainedAdapter(primary=primary, secondary=secondary)
    # primary.fetch_bulk_quotes will fail because HPG is in fail list
    quotes = await chain.fetch_bulk_quotes(["HPG", "VNM"])
    assert len(quotes) == 2


async def test_vci_only_in_trading_hours_fallback_outside() -> None:
    """VCI-first: trong giờ GD chỉ nguồn chính; ngoài giờ mới dùng nguồn dự phòng."""
    trading = {"now": True}
    primary = MockAdapter(fail_tickers={"HPG"})
    secondary = MockAdapter()
    chain = ChainedAdapter(primary, secondary, fallback_allowed=lambda: not trading["now"])

    with pytest.raises(ValueError):
        await chain.fetch_quote("HPG")  # trong giờ: không rơi sang KBS
    with pytest.raises(ValueError):
        await chain.fetch_bulk_quotes(["HPG", "VNM"])

    trading["now"] = False
    assert (await chain.fetch_quote("HPG")).ticker == "HPG"
    assert len(await chain.fetch_bulk_quotes(["HPG", "VNM"])) == 2


async def test_policy_error_allows_fallback() -> None:
    def broken() -> bool:
        raise RuntimeError("calendar down")

    chain = ChainedAdapter(
        MockAdapter(fail_tickers={"HPG"}), MockAdapter(), fallback_allowed=broken
    )
    assert (await chain.fetch_quote("HPG")).ticker == "HPG"


def test_factory_wires_trading_hours_policy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.market.adapters import factory
    from src.market.trading_calendar import VNTradingCalendar
    from src.platform.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "mock_market", False)
    chain = factory.build_adapter()
    assert isinstance(chain, ChainedAdapter)
    monkeypatch.setattr(
        VNTradingCalendar, "is_trading_now", classmethod(lambda cls, now=None: True)
    )
    assert len(chain._active()) == 1
    monkeypatch.setattr(
        VNTradingCalendar, "is_trading_now", classmethod(lambda cls, now=None: False)
    )
    assert len(chain._active()) == 3
