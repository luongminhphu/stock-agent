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
