"""Wave 9.2 — format_for_prompt() phải cho AI biết phần cp không bán được.

Mọi AI agent nhận portfolio qua ContextBuilder._fetch_portfolio_bias() →
PortfolioContext.format_for_prompt(). Neu prompt khong noi ro "KHONG BAN
DUOC", AI se khuyen nghi exit/reduce tren vi the bi khoa (ESOP, phat hanh
them...) — khuyen nghi khong kha thi, gay nhieu tin hieu.
"""

from __future__ import annotations

from datetime import date

from src.portfolio.models import PortfolioContext, PositionSummary


def _ctx_with(*positions: PositionSummary) -> PortfolioContext:
    return PortfolioContext(
        user_id="u1",
        open_positions=list(positions),
        position_count=len(positions),
    )


def test_locked_position_renders_lock_note_for_ai():
    ctx = _ctx_with(
        PositionSummary(
            ticker="HPG",
            qty=5000.0,
            avg_cost=28_000.0,
            sector="Materials",
            thesis_id=None,
            locked_qty=2000.0,
            locked_reason="esop",
            locked_until=date(2026, 12, 15),
        )
    )

    out = ctx.format_for_prompt()

    assert "KHÔNG BÁN ĐƯỢC 2,000 cp" in out
    assert "(esop)" in out
    assert "2026-12-15" in out
    assert "chỉ bán được 3,000 cp" in out


def test_unlocked_position_prompt_unchanged():
    ctx = _ctx_with(
        PositionSummary(
            ticker="VCB",
            qty=500.0,
            avg_cost=87_500.0,
            sector="Banking",
            thesis_id=None,
        )
    )

    out = ctx.format_for_prompt()

    assert "KHÔNG BÁN ĐƯỢC" not in out
    assert "VCB: 500 cp @ 87,500 [Banking]" in out


def test_sellable_qty_never_negative():
    p = PositionSummary(
        ticker="X",
        qty=100.0,
        avg_cost=10_000.0,
        sector=None,
        thesis_id=None,
        locked_qty=150.0,  # du lieu xau: locked > qty
    )
    assert p.sellable_qty == 0.0
