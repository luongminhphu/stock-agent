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


# ---------------------------------------------------------------------------
# P1 — intended_action + PortfolioContext + hard gate theo lệnh
# ---------------------------------------------------------------------------


def _position(qty: float = 3000, avg_cost: float = 25_000.0) -> MagicMock:
    p = MagicMock()
    p.qty = qty
    p.avg_cost = avg_cost
    p.thesis_id = 7
    p.sector = "Thép"
    return p


def _p1_service(position: MagicMock | None, ai_action: str = "BUY") -> PreTradeService:
    qs = AsyncMock()
    qs.get_quote.return_value = _quote(price=27_500.0)
    svc = _make_service(qs, None)
    svc._agent.check.return_value.intended_action = ai_action
    svc._agent.check.return_value.proceed_recommendation = True
    svc._agent.check.return_value.blocking_issues = []
    svc._fetch_open_position = AsyncMock(return_value=position)  # type: ignore[method-assign]
    return svc


@pytest.mark.asyncio
async def test_sell_without_position_is_blocked_and_action_kept():
    svc = _p1_service(position=None, ai_action="BUY")  # AI "đổi" sang BUY → phải khôi phục
    result = await svc.check("hpg", "u1", intended_action="SELL", quantity=1000)
    assert str(result.intended_action) == "SELL"
    assert result.proceed_recommendation is False
    assert any("Không có vị thế" in b for b in result.blocking_issues)
    # Bán không tính sizing
    svc._compute_sizing.assert_not_awaited()
    kwargs = svc._agent.check.await_args.kwargs
    assert kwargs["intended_action"] == "SELL" and kwargs["quantity"] == 1000
    assert kwargs["portfolio_context"] == ""


@pytest.mark.asyncio
async def test_sell_with_position_passes_portfolio_context():
    svc = _p1_service(position=_position(), ai_action="SELL")
    result = await svc.check("HPG", "u1", intended_action="sell")
    assert result.proceed_recommendation is True and result.blocking_issues == []
    ctx = svc._agent.check.await_args.kwargs["portfolio_context"]
    assert "3,000 cp" in ctx and "25,000" in ctx and "+10.0%" in ctx and "thesis #7" in ctx


@pytest.mark.asyncio
async def test_reduce_full_quantity_is_blocked():
    svc = _p1_service(position=_position(qty=1000), ai_action="REDUCE")
    result = await svc.check("HPG", "u1", intended_action="REDUCE", quantity=1000)
    assert result.proceed_recommendation is False
    assert any("BÁN toàn bộ" in b for b in result.blocking_issues)


@pytest.mark.asyncio
async def test_buy_still_runs_sizing_and_defaults():
    svc = _p1_service(position=None)
    await svc.check("HPG", "u1")  # caller cũ: không truyền action → BUY
    svc._compute_sizing.assert_awaited_once()
    assert svc._agent.check.await_args.kwargs["intended_action"] == "BUY"


@pytest.mark.asyncio
async def test_invalid_action_rejected():
    svc = _p1_service(position=None)
    with pytest.raises(ValueError, match="intended_action"):
        await svc.check("HPG", "u1", intended_action="SHORT")
