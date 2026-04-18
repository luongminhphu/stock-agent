"""Integration tests for briefing.BriefingService.

Uses:
 - in-memory SQLite (from conftest.py)
 - MockPerplexityClient + stub QuoteService
 - Real BriefingAgent, BriefingService, WatchlistService
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from src.ai.agents.briefing import BriefingAgent
from src.ai.schemas import MarketSentiment
from src.briefing.service import BriefingService
from src.watchlist.service import AddToWatchlistInput, WatchlistService
from tests.ai.conftest import MockPerplexityClient

USER = "brief_user"


@dataclass
class _FakeQuote:
    ticker: str
    price: float
    change: float = 500.0
    change_pct: float = 1.2
    volume: int = 5_000_000
    timestamp: datetime = datetime.utcnow()


class _StubQuoteService:
    def __init__(self, tickers: list[str]) -> None:
        self._tickers = tickers

    async def get_bulk_quotes(self, tickers: list[str]) -> list[_FakeQuote]:
        return [_FakeQuote(ticker=t, price=50_000) for t in tickers if t in self._tickers]


def _make_brief_payload(sentiment: str = "MIXED") -> dict:
    return {
        "headline": "VN-Index dao động nhẹ trong phiên sáng",
        "sentiment": sentiment,
        "summary": "Thị trường thận trọng. Thanh khoản ở mức trung bình.",
        "key_movers": ["VCB", "HPG"],
        "watchlist_alerts": ["HPG vượt MA20"],
        "action_items": ["Review HPG stop-loss"],
    }


async def test_morning_brief_with_watchlist(session):
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="HPG"))
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="VCB"))
    await session.flush()

    mock = MockPerplexityClient(_make_brief_payload())
    agent = BriefingAgent(mock)
    svc = BriefingService(
        watchlist_service=wl_svc,
        quote_service=_StubQuoteService(["HPG", "VCB"]),
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id=USER)

    assert result.sentiment == MarketSentiment.MIXED
    assert result.headline != ""
    assert len(result.key_movers) == 2


async def test_morning_brief_tickers_in_agent_message(session):
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="FPT"))
    await session.flush()

    mock = MockPerplexityClient(_make_brief_payload())
    agent = BriefingAgent(mock)
    svc = BriefingService(
        watchlist_service=wl_svc,
        quote_service=_StubQuoteService(["FPT"]),
        briefing_agent=agent,
    )
    await svc.generate_morning_brief(user_id=USER)

    user_msg = mock.calls[0]["messages"][1]["content"]
    assert "FPT" in user_msg


async def test_morning_brief_empty_watchlist(session):
    mock = MockPerplexityClient(_make_brief_payload("UNCERTAIN"))
    agent = BriefingAgent(mock)
    svc = BriefingService(
        watchlist_service=WatchlistService(session),
        quote_service=_StubQuoteService([]),
        briefing_agent=agent,
    )
    result = await svc.generate_morning_brief(user_id=USER)
    assert result.sentiment == MarketSentiment.UNCERTAIN


async def test_eod_brief_with_watchlist(session):
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="VNM"))
    await session.flush()

    mock = MockPerplexityClient(_make_brief_payload("RISK_OFF"))
    agent = BriefingAgent(mock)
    svc = BriefingService(
        watchlist_service=wl_svc,
        quote_service=_StubQuoteService(["VNM"]),
        briefing_agent=agent,
    )
    result = await svc.generate_eod_brief(user_id=USER)
    assert result.sentiment == MarketSentiment.RISK_OFF


async def test_brief_quote_failure_degrades_gracefully(session):
    """If QuoteService raises, brief should still be generated (degraded context)."""
    class _FailingQuoteService:
        async def get_bulk_quotes(self, _):
            raise RuntimeError("Network error")

    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="TCB"))
    await session.flush()

    mock = MockPerplexityClient(_make_brief_payload())
    agent = BriefingAgent(mock)
    svc = BriefingService(
        watchlist_service=wl_svc,
        quote_service=_FailingQuoteService(),
        briefing_agent=agent,
    )
    # Should NOT raise — degraded context is passed to agent
    result = await svc.generate_morning_brief(user_id=USER)
    assert result is not None

    user_msg = mock.calls[0]["messages"][1]["content"]
    assert "thiếu dữ liệu" in user_msg


async def test_eod_brief_market_context_contains_snapshot(session):
    """Market context passed to agent should include ticker snapshot data."""
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="MSN"))
    await session.flush()

    mock = MockPerplexityClient(_make_brief_payload())
    agent = BriefingAgent(mock)
    svc = BriefingService(
        watchlist_service=wl_svc,
        quote_service=_StubQuoteService(["MSN"]),
        briefing_agent=agent,
    )
    await svc.generate_eod_brief(user_id=USER)

    user_msg = mock.calls[0]["messages"][1]["content"]
    assert "MSN" in user_msg
    assert "giá=" in user_msg
