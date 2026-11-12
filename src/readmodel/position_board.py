"""PositionBoard — bảng lõi "Danh mục & Watchlist": vị thế + tiền lệ của từng mã.

Owner: readmodel (projection đọc chéo portfolio × watchlist × market.baserate).

Mỗi dòng trả lời 3 câu hỏi của nhà đầu tư nắm giữ trung hạn:
  1. Tôi đang ở đâu? — giá, lãi/lỗ, tỉ trọng (mã đang nắm).
  2. Lịch sử của CHÍNH mã này nói gì về trạng thái hiện tại? — xác suất lãi sau
     60/120 phiên, lợi thế so với mức nền của mã, độ tin cậy (số đợt độc lập), gợi ý.
  3. Mức lỗ hiện tại có bất thường không? — so mức sụt từ giá vốn với mức sụt trung
     bình mà các tiền lệ từng chịu trước khi kết thúc kỳ nắm giữ.

Không gọi AI. Tiền lệ lấy từ market.BaseRateService (đã cache theo phiên đã đóng cửa)
— cùng một nguồn với drawer chi tiết và kế hoạch, nên gợi ý luôn khớp nhau.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from src.market.baserate import FEATURE_LABELS, SUGGEST_INSUFFICIENT
from src.platform.logging import get_logger

logger = get_logger(__name__)

MAIN_HORIZON = 120
SHORT_HORIZON = 60
_MAX_CONCURRENT = 3


@dataclass
class BoardRow:
    ticker: str
    in_portfolio: bool
    in_watchlist: bool
    qty: float | None = None
    avg_cost: float | None = None
    price: float | None = None
    market_value: float | None = None
    weight: float | None = None  # % giá trị danh mục
    pnl_pct: float | None = None
    suggestion: str = SUGGEST_INSUFFICIENT
    confidence: str = "khong_du"
    state: dict[str, str] = field(default_factory=dict)
    matched_on: list[str] = field(default_factory=list)
    episodes: int = 0
    win_prob_60: float | None = None
    win_prob_120: float | None = None
    base_win_120: float | None = None
    edge_120: float | None = None
    median_120: float | None = None
    p10_120: float | None = None
    p90_120: float | None = None
    typical_drawdown: float | None = None  # mức sụt trung bình của tiền lệ trong kỳ 120 phiên
    drawdown_flag: str | None = None  # binh_thuong | sau_hon_tien_le (chỉ mã đang lỗ)
    error: str | None = None


def build_rows(
    positions: list[Any],
    watchlist: list[str],
    prices: dict[str, float],
    reports: dict[str, Any],
    errors: dict[str, str] | None = None,
) -> list[BoardRow]:
    """Thuần: ghép vị thế × watchlist × giá × báo cáo tiền lệ thành các dòng đã sắp xếp."""
    errors = errors or {}
    held = {p.ticker.upper(): p for p in positions}
    tickers = list(held) + [t.upper() for t in watchlist if t.upper() not in held]
    wl = {t.upper() for t in watchlist}
    values = {t: float(p.qty) * prices.get(t, p.avg_cost) for t, p in held.items()}
    total = sum(values.values())
    rows: list[BoardRow] = []
    for t in dict.fromkeys(tickers):
        pos = held.get(t)
        px = prices.get(t)
        row = BoardRow(ticker=t, in_portfolio=pos is not None, in_watchlist=t in wl, price=px)
        if pos is not None:
            row.qty, row.avg_cost = float(pos.qty), float(pos.avg_cost)
            row.market_value = round(values[t])
            row.weight = round(values[t] / total * 100, 2) if total else None
            if px and pos.avg_cost:
                row.pnl_pct = round((px / pos.avg_cost - 1) * 100, 2)
        rep = reports.get(t)
        if rep is not None:
            row.suggestion, row.confidence = rep.suggestion, rep.confidence
            row.state = {k: FEATURE_LABELS.get(k, {}).get(v, v) for k, v in rep.state.items()}
            row.matched_on = list(rep.matched_on)
            o120 = rep.outcomes.get(MAIN_HORIZON)
            o60 = rep.outcomes.get(SHORT_HORIZON)
            if o60 is not None:
                row.win_prob_60 = o60.win_prob
            if o120 is not None:
                row.episodes = o120.episodes
                row.win_prob_120, row.base_win_120 = o120.win_prob, o120.base_win_rate
                row.edge_120, row.median_120 = o120.edge, o120.median_return
                row.p10_120, row.p90_120 = o120.p10_return, o120.p90_return
                row.typical_drawdown = o120.avg_max_drawdown
        if row.pnl_pct is not None and row.pnl_pct < 0 and row.typical_drawdown is not None:
            row.drawdown_flag = (
                "sau_hon_tien_le" if row.pnl_pct / 100 < row.typical_drawdown else "binh_thuong"
            )
        row.error = errors.get(t)
        rows.append(row)
    rows.sort(
        key=lambda r: (
            not r.in_portfolio,
            -(r.weight or 0) if r.in_portfolio else -(r.edge_120 or -1),
        )
    )
    return rows


def summarize(rows: list[BoardRow]) -> dict[str, Any]:
    held = [r for r in rows if r.in_portfolio]
    total = sum(r.market_value or 0 for r in held)
    weighted = [(r.market_value or 0, r.win_prob_120) for r in held if r.win_prob_120 is not None]
    wsum = sum(v for v, _ in weighted)
    return {
        "holdings": len(held),
        "watchlist_only": len(rows) - len(held),
        "portfolio_value": round(total),
        "weighted_win_prob_120": round(sum(v * p for v, p in weighted) / wsum, 4) if wsum else None,
        "lean_avoid_holdings": [r.ticker for r in held if r.suggestion == "NGHIENG_TRANH"],
        "lean_buy_watchlist": [
            r.ticker for r in rows if not r.in_portfolio and r.suggestion == "NGHIENG_MUA"
        ],
        "abnormal_drawdowns": [r.ticker for r in held if r.drawdown_flag == "sau_hon_tien_le"],
        "insufficient": [r.ticker for r in rows if r.suggestion == SUGGEST_INSUFFICIENT],
    }


class PositionBoardService:
    def __init__(self, session: Any, quote_service: Any, baserate_service: Any) -> None:
        self._session = session
        self._quotes = quote_service
        self._baserate = baserate_service
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _report(self, ticker: str) -> Any:
        async with self._sem:
            return await self._baserate.analyze(ticker)

    async def get_board(self, user_id: str, today: date) -> dict[str, Any]:
        from src.portfolio.repository import PortfolioRepository  # noqa: PLC0415
        from src.watchlist.repository import WatchlistRepository  # noqa: PLC0415

        positions = await PortfolioRepository(self._session).list_open_positions(user_id)
        items = await WatchlistRepository(self._session).list_for_user(user_id)
        watch = [i.ticker.upper() for i in items]
        tickers = list(dict.fromkeys([p.ticker.upper() for p in positions] + watch))

        prices: dict[str, float] = {}
        if tickers:
            try:
                prices = {
                    q.ticker.upper(): q.settled_price
                    for q in await self._quotes.get_bulk_quotes(tickers)
                }
            except Exception as exc:  # noqa: BLE001 — thiếu giá vẫn trả tiền lệ
                logger.warning("position_board.quotes_failed", error=str(exc))

        reports: dict[str, Any] = {}
        errors: dict[str, str] = {}

        async def one(t: str) -> None:
            try:
                reports[t] = await self._report(t)
            except Exception as exc:  # noqa: BLE001
                errors[t] = str(exc)[:200]

        await asyncio.gather(*(one(t) for t in tickers))
        rows = build_rows(positions, watch, prices, reports, errors)
        return {
            "as_of": today.isoformat(),
            "summary": summarize(rows),
            "rows": [asdict(r) for r in rows],
        }
