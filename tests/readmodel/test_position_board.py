"""PositionBoard: ghép vị thế × watchlist × tiền lệ; cờ mức sụt bất thường; tóm tắt."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.market.baserate import BaseRateReport, Outcome
from src.readmodel.position_board import PositionBoardService, build_rows, summarize


def _report(ticker: str, suggestion: str, win: float, edge: float, mdd: float) -> BaseRateReport:
    o = Outcome(
        120, 12, win, win, win - edge, edge, win - 0.1, win + 0.1, 0.05, -0.1, 0.2, 0.01, mdd
    )
    o60 = Outcome(60, 20, 0.55, 0.55, 0.5, 0.05, 0.45, 0.65, 0.02, -0.05, 0.1, 0.0, -0.05)
    return BaseRateReport(
        ticker=ticker,
        as_of=date(2026, 11, 10),
        state={"trend": "below_both"},
        matched_on=("trend",),
        dropped=(),
        matching_days=40,
        outcomes={60: o60, 120: o},
        analogs=[],
        suggestion=suggestion,
        confidence="trung_binh",
        history_from=date(2016, 1, 1),
    )


def _pos(t: str, qty: float, cost: float) -> SimpleNamespace:
    return SimpleNamespace(ticker=t, qty=qty, avg_cost=cost)


def test_build_rows_orders_holdings_then_watchlist_and_flags_drawdown() -> None:
    rows = build_rows(
        positions=[_pos("FPT", 100, 100_000), _pos("hpg", 1000, 30_000)],
        watchlist=["VNM", "FPT", "MWG"],
        prices={"FPT": 80_000, "HPG": 31_000, "VNM": 60_000},
        reports={
            "FPT": _report("FPT", "NGHIENG_TRANH", 0.35, -0.15, -0.12),
            "HPG": _report("HPG", "TRUNG_TINH", 0.5, 0.0, -0.08),
            "VNM": _report("VNM", "NGHIENG_MUA", 0.7, 0.2, -0.06),
        },
        errors={"MWG": "no data"},
    )
    assert [r.ticker for r in rows] == ["HPG", "FPT", "VNM", "MWG"]  # HPG tỉ trọng lớn hơn
    fpt = next(r for r in rows if r.ticker == "FPT")
    assert fpt.in_portfolio and fpt.in_watchlist
    assert fpt.pnl_pct == -20.0
    assert fpt.drawdown_flag == "sau_hon_tien_le", "lỗ 20% sâu hơn mức sụt tiền lệ 12%"
    assert fpt.state == {"trend": "Giá dưới MA50 và MA200"}
    hpg = rows[0]
    assert hpg.drawdown_flag is None and hpg.weight == pytest.approx(
        31_000_000 / 39_000_000 * 100, abs=0.01
    )
    assert rows[-1].error == "no data" and rows[-1].suggestion == "KHONG_DU_TIEN_LE"

    s = summarize(rows)
    assert s["holdings"] == 2 and s["watchlist_only"] == 2
    assert s["lean_avoid_holdings"] == ["FPT"]
    assert s["lean_buy_watchlist"] == ["VNM"]
    assert s["abnormal_drawdowns"] == ["FPT"]
    assert s["insufficient"] == ["MWG"]
    assert 0.35 < s["weighted_win_prob_120"] < 0.5


@pytest.mark.anyio
async def test_service_reads_positions_and_watchlist(session) -> None:  # type: ignore[no-untyped-def]
    from src.portfolio.models import Position
    from src.watchlist.models import WatchlistItem

    session.add_all(
        [
            Position(user_id="u1", ticker="FPT", qty=10, avg_cost=100.0),
            WatchlistItem(user_id="u1", ticker="VNM"),
            WatchlistItem(user_id="u2", ticker="SSI"),
        ]
    )
    await session.flush()

    class _Quotes:
        async def get_bulk_quotes(self, tickers):  # type: ignore[no-untyped-def]
            return [SimpleNamespace(ticker=t, settled_price=110.0) for t in tickers]

    calls: list[str] = []

    class _BR:
        async def analyze(self, t):  # type: ignore[no-untyped-def]
            calls.append(t)
            return _report(t, "TRUNG_TINH", 0.5, 0.0, -0.1)

    svc = PositionBoardService(session, _Quotes(), _BR())
    out = await svc.get_board("u1", date(2026, 11, 10))
    assert [r["ticker"] for r in out["rows"]] == ["FPT", "VNM"]
    assert out["rows"][0]["pnl_pct"] == 10.0
    await svc.get_board("u1", date(2026, 11, 10))
    # Bảng không giữ cache riêng: luôn hỏi BaseRateService (service tự cache theo phiên
    # đã đóng cửa) → gợi ý trên bảng khớp với drawer chi tiết.
    assert sorted(calls) == ["FPT", "FPT", "VNM", "VNM"]
