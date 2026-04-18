"""Unit tests for BriefingService."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.schemas import BriefOutput, MarketSentiment
from src.briefing.service import BriefingService


@pytest.fixture
def sample_brief() -> BriefOutput:
    return BriefOutput(
        headline="Dòng tiền thăm dò trở lại nhóm thép",
        sentiment=MarketSentiment.MIXED,
        summary="Thị trường giằng co nhưng một số mã trong watchlist có tín hiệu hồi phục.",
        key_movers=["HPG +2.1%", "FPT -1.3%"],
        watchlist_alerts=["HPG vượt MA20 intraday"],
        action_items=["Theo dõi thêm thanh khoản HPG phiên chiều"],
    )


@pytest.mark.asyncio
async def test_generate_morning_brief_success(sample_brief: BriefOutput) -> None:
    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = [
        SimpleNamespace(ticker="HPG"),
        SimpleNamespace(ticker="FPT"),
    ]

    quote_service = AsyncMock()
    quote_service.get_bulk_quotes.return_value = [
        SimpleNamespace(ticker="HPG", price=28000, change=500, change_pct=1.82, volume=12000000),
        SimpleNamespace(ticker="FPT", price=118000, change=-1500, change_pct=-1.26, volume=2300000),
    ]

    agent = AsyncMock()
    agent.generate_morning_brief.return_value = sample_brief

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id="u1")

    assert result.headline == sample_brief.headline
    watchlist_service.list_items.assert_called_once_with(user_id="u1")
    quote_service.get_bulk_quotes.assert_called_once_with(["HPG", "FPT"])
    agent.generate_morning_brief.assert_called_once()
    context = agent.generate_morning_brief.call_args.kwargs["market_context"]
    assert "HPG" in context
    assert "FPT" in context


@pytest.mark.asyncio
async def test_generate_eod_brief_with_empty_watchlist(sample_brief: BriefOutput) -> None:
    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = []

    quote_service = AsyncMock()
    agent = AsyncMock()
    agent.generate_eod_brief.return_value = sample_brief

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_eod_brief(user_id="u1")

    assert result.summary == sample_brief.summary
    quote_service.get_bulk_quotes.assert_not_called()
    context = agent.generate_eod_brief.call_args.kwargs["market_context"]
    assert "watchlist" in context.lower()


@pytest.mark.asyncio
async def test_generate_brief_quote_failure_falls_back(sample_brief: BriefOutput) -> None:
    watchlist_service = AsyncMock()
    watchlist_service.list_items.return_value = [SimpleNamespace(ticker="VCB")]

    quote_service = AsyncMock()
    quote_service.get_bulk_quotes.side_effect = RuntimeError("market unavailable")

    agent = AsyncMock()
    agent.generate_morning_brief.return_value = sample_brief

    svc = BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_service,
        briefing_agent=agent,
    )

    result = await svc.generate_morning_brief(user_id="u1")

    assert result.key_movers == sample_brief.key_movers
    context = agent.generate_morning_brief.call_args.kwargs["market_context"]
    assert "thiếu dữ liệu" in context.lower() or "không lấy được quote" in context.lower()
