"""Boundary fix B6: readmodel.universe_query cung cấp known tickers cho market.registry."""

from __future__ import annotations

import pytest

from src.market.registry_loader import RegistryLoader
from src.market.registry_types import Exchange, Sector, SymbolInfo
from src.platform.db import AsyncSessionLocal
from src.readmodel.universe_query import known_tickers, make_known_tickers_provider
from src.watchlist.models import WatchlistItem


@pytest.mark.anyio
async def test_known_tickers_merges_watchlist(monkeypatch) -> None:
    async with AsyncSessionLocal() as s:
        s.add(WatchlistItem(user_id="u1", ticker="hpg"))
        await s.commit()
        assert "HPG" in await known_tickers(s)

    provider = make_known_tickers_provider(AsyncSessionLocal)
    assert "HPG" in await provider()


@pytest.mark.anyio
async def test_registry_loader_adds_db_tickers_via_provider() -> None:
    async def provider() -> set[str]:
        return {"HPG", "ABC"}

    seed = {
        "HPG": SymbolInfo(
            ticker="HPG",
            name="Hoa Phat",
            exchange=Exchange.HOSE,
            sector=Sector.OTHER,
            key_metrics="",
        )
    }
    new = await RegistryLoader(known_tickers_provider=provider)._fetch_db_tickers(seed)
    assert set(new) == {"ABC"} and new["ABC"].sector == Sector.OTHER
