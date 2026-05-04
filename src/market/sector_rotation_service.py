"""Sector Rotation Service.

Aggregate quote data theo sector, tính avg change, xác định flow direction.
Output là raw SectorFlow list để inject vào SectorRotationAgent.

Owner: market segment.
Callers: ai.agents.sector_rotation, briefing (context injection).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from src.market.quote_service import QuoteService
from src.market.registry import TickerRegistry
from src.ai.schemas import FlowDirection, SectorFlow

logger = logging.getLogger(__name__)

# Số tickers để lấy top movers
_TOP_MOVER_COUNT = 3

# Threshold tuyệt đối để xác định flow direction (% change)
_FLOW_THRESHOLD = 0.3


class SectorRotationService:
    """Tổng hợp dữ liệu giá theo sector để phân tích rotation.

    Không chứa AI logic — chỉ aggregate raw quote data.
    AI inference nằm trong SectorRotationAgent.
    """

    def __init__(
        self,
        quote_service: QuoteService,
        registry: TickerRegistry,
    ) -> None:
        self._quotes = quote_service
        self._registry = registry

    async def get_sector_flows(
        self,
        watchlist_tickers: list[str] | None = None,
    ) -> list[SectorFlow]:
        """Lấy SectorFlow cho tất cả sectors hiện có trong registry.

        Args:
            watchlist_tickers: Nếu cung cấp, chỉ aggregate sectors có
                ít nhất 1 ticker trong watchlist (narrow scope). Nếu None,
                lấy toàn bộ registry.

        Returns:
            List SectorFlow, sorted by avg_change_pct_1d descending.
        """
        # 1. Lấy sector map từ registry
        sector_map: dict[str, list[str]] = self._registry.get_sector_map()

        if watchlist_tickers:
            watchlist_set = set(t.upper() for t in watchlist_tickers)
            # Chỉ giữ sectors có overlap với watchlist
            sector_map = {
                sector: tickers
                for sector, tickers in sector_map.items()
                if watchlist_set & set(tickers)
            }

        if not sector_map:
            logger.warning("sector_rotation: empty sector_map, returning []")
            return []

        # 2. Collect all tickers cần query (dedup)
        all_tickers = list({t for tickers in sector_map.values() for t in tickers})

        # 3. Bulk fetch quotes — single network round-trip
        try:
            raw = await self._quotes.get_bulk_quotes(all_tickers)
        except Exception:
            logger.exception("sector_rotation: get_bulk_quotes failed")
            return []

        quote_map = {q.ticker: q for q in raw}

        # 4. Aggregate per sector
        flows: list[SectorFlow] = []
        for sector, tickers in sector_map.items():
            changes: list[tuple[str, float]] = []
            for ticker in tickers:
                q = quote_map.get(ticker)
                if q is not None and q.change_pct is not None:
                    changes.append((ticker, q.change_pct))

            if not changes:
                continue

            avg = sum(c for _, c in changes) / len(changes)
            flow = _classify_flow(avg)

            # Top movers: sort by abs change, take top N
            top = sorted(changes, key=lambda x: abs(x[1]), reverse=True)
            top_movers = [ticker for ticker, _ in top[:_TOP_MOVER_COUNT]]

            flows.append(
                SectorFlow(
                    sector=sector,
                    avg_change_pct_1d=round(avg, 2),
                    flow_direction=flow,
                    top_movers=top_movers,
                    ticker_count=len(changes),
                )
            )

        # Sort: INFLOW sectors trước, desc by avg change
        flows.sort(key=lambda f: f.avg_change_pct_1d, reverse=True)
        return flows

    async def get_snapshot_date(self) -> str:
        """Trả về ngày trading gần nhất dạng YYYY-MM-DD."""
        return date.today().isoformat()


def _classify_flow(avg_change_pct: float) -> FlowDirection:
    """Phân loại flow direction dựa trên avg % change."""
    if avg_change_pct >= _FLOW_THRESHOLD:
        return FlowDirection.INFLOW
    if avg_change_pct <= -_FLOW_THRESHOLD:
        return FlowDirection.OUTFLOW
    return FlowDirection.NEUTRAL
