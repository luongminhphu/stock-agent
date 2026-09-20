"""Wave C2: PreTradeService dùng TickerContext làm nguồn giá + bối cảnh kỹ thuật.

Mọi context builder / sizing / persist được mock trên instance — chỉ kiểm tra
luồng quote → agent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.thesis.pretrade_service import PreTradeService


def _quote(price: float = 70_000.0, change_pct: float = 0.72) -> MagicMock:
    q = MagicMock()
    q.price = price
    q.change_pct = change_pct
    return q


def _ctx(line: str = "VNM 70,000 (+0.72%) | trend UP · data live") -> MagicMock:
    c = MagicMock()
    c.quote = _quote()
    c.format_for_prompt.return_value = line
    return c


def _make_service(quote_service: AsyncMock, tcs: AsyncMock | None) -> PreTradeService:
    agent = AsyncMock()
    result = MagicMock()
    result.intended_action = "BUY"
    result.confidence = 0.7
    result.sizing_note = None
    agent.check.return_value = result
    svc = PreTradeService(
        session=MagicMock(),
        quote_service=quote_service,
        pretrade_agent=agent,
        ticker_context_service=tcs,
    )
    for name in (
        "_build_thesis_context",
        "_build_signal_context",
        "_build_brief_context",
        "_build_lesson_context",
        "_build_market_context",
    ):
        setattr(svc, name, AsyncMock(return_value=""))
    svc._compute_sizing = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._persist_advice = AsyncMock()  # type: ignore[method-assign]
    return svc


@pytest.mark.asyncio
async def test_check_uses_ticker_context_quote_and_line():
    qs = AsyncMock()
    tcs = AsyncMock()
    tcs.get.return_value = _ctx()
    svc = _make_service(qs, tcs)

    await svc.check(ticker="vnm", user_id="u1")

    tcs.get.assert_awaited_once_with("VNM")
    qs.get_quote.assert_not_awaited()
    kw = svc._agent.check.call_args.kwargs
    assert kw["price"] == 70_000.0 and kw["change_pct"] == 0.72
    assert kw["ticker_context"] == "VNM 70,000 (+0.72%) | trend UP · data live"


@pytest.mark.asyncio
async def test_check_falls_back_to_quote_when_context_fails():
    qs = AsyncMock()
    qs.get_quote.return_value = _quote(price=69_000.0, change_pct=-0.5)
    tcs = AsyncMock()
    tcs.get.side_effect = RuntimeError("ohlcv down")
    svc = _make_service(qs, tcs)

    await svc.check(ticker="VNM", user_id="u1")

    qs.get_quote.assert_awaited_once_with("VNM")
    kw = svc._agent.check.call_args.kwargs
    assert kw["price"] == 69_000.0 and kw["ticker_context"] == ""


@pytest.mark.asyncio
async def test_check_without_context_service_keeps_legacy_path():
    qs = AsyncMock()
    qs.get_quote.return_value = _quote()
    svc = _make_service(qs, None)

    await svc.check(ticker="VNM", user_id="u1")

    qs.get_quote.assert_awaited_once_with("VNM")
    assert svc._agent.check.call_args.kwargs["ticker_context"] == ""
