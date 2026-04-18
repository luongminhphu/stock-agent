"""Briefing service — owner of brief generation flow.

Responsibilities:
- collect watchlist tickers from watchlist segment
- collect market context from market segment (quotes)
- call BriefingAgent for morning/EOD narrative
- return structured BriefOutput to adapters

Non-responsibilities:
- no Discord formatting (see formatter.py)
- no HTTP route logic
- no scheduler logic
"""

from __future__ import annotations

from datetime import datetime

from src.ai.agents.briefing import BriefingAgent
from src.ai.schemas import BriefOutput
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)


class BriefingService:
    """Orchestrates morning and end-of-day brief generation."""

    def __init__(
        self,
        watchlist_service: WatchlistService,
        quote_service: object,
        briefing_agent: BriefingAgent,
    ) -> None:
        self._watchlist_service = watchlist_service
        self._quote_service = quote_service
        self._agent = briefing_agent

    async def generate_morning_brief(self, user_id: str) -> BriefOutput:
        tickers = await self._get_watchlist_tickers(user_id)
        market_context = await self._build_market_context(tickers, phase="morning")
        logger.info("briefing.generate_morning", user_id=user_id, tickers=tickers)
        # FIX: agent method is morning_brief(), not generate_morning_brief()
        return await self._agent.morning_brief(
            market_context=market_context,
            watchlist_tickers=tickers,
        )

    async def generate_eod_brief(self, user_id: str) -> BriefOutput:
        tickers = await self._get_watchlist_tickers(user_id)
        market_context = await self._build_market_context(tickers, phase="eod")
        logger.info("briefing.generate_eod", user_id=user_id, tickers=tickers)
        # FIX: agent method is eod_brief(), not generate_eod_brief()
        return await self._agent.eod_brief(
            market_context=market_context,
            watchlist_tickers=tickers,
        )

    async def _get_watchlist_tickers(self, user_id: str) -> list[str]:
        items = await self._watchlist_service.list_items(user_id=user_id)
        return [item.ticker for item in items]

    async def _build_market_context(self, tickers: list[str], phase: str) -> str:
        now = datetime.now().strftime("%H:%M %d/%m/%Y")
        if not tickers:
            return (
                f"Thời điểm: {now}. Không có mã nào trong watchlist. "
                f"Hãy viết {phase} brief ở mức thị trường chung, nhấn mạnh quản trị rủi ro."
            )

        try:
            quotes = await self._quote_service.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("briefing.quote_fetch_failed", tickers=tickers, error=str(exc))
            return (
                f"Thời điểm: {now}. Không lấy được quote cho watchlist {', '.join(tickers)}. "
                f"Hãy viết {phase} brief thận trọng, nêu rõ thiếu dữ liệu giá realtime."
            )

        lines = [f"Thời điểm: {now}. Pha: {phase}.", "Watchlist snapshot:"]
        for q in quotes:
            volume = getattr(q, "volume", None)
            volume_text = f", volume={volume:,}" if volume is not None else ""
            lines.append(
                f"- {q.ticker}: giá={q.price:,.0f}, change={q.change:,.0f}, "
                f"change_pct={q.change_pct:.2f}%{volume_text}"
            )
        lines.append(
            "Tập trung vào mã biến động mạnh, tín hiệu risk-on/risk-off, và watchlist-specific alerts."
        )
        return "\n".join(lines)
