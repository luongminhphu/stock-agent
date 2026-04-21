"""Briefing service — owner of brief generation flow.

Owner: briefing segment.

Responsibilities:
- collect watchlist tickers from watchlist segment
- collect market context from market segment (quotes)
- call BriefingAgent for morning/EOD narrative
- persist BriefSnapshot via BriefSnapshotRepository
- return structured BriefOutput to adapters

Non-responsibilities:
- no Discord formatting (see formatter.py)
- no HTTP route logic
- no scheduler logic
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.briefing import BriefingAgent
from src.ai.schemas import BriefOutput
from src.briefing.models import BriefSnapshot
from src.briefing.repository import BriefSnapshotRepository
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService
from src.market.registry import registry as symbol_registry

logger = get_logger(__name__)


class BriefingService:
    """Orchestrates morning and end-of-day brief generation.

    Args:
        watchlist_service:  reads user watchlist tickers.
        quote_service:      fetches bulk market quotes.
        briefing_agent:     AI agent that writes the brief narrative.
        session:            AsyncSession for persisting BriefSnapshot.
                            Pass None to skip persistence (e.g. in tests).
    """

    def __init__(
        self,
        watchlist_service: WatchlistService,
        quote_service: object,
        briefing_agent: BriefingAgent,
        session: AsyncSession | None = None,
    ) -> None:
        self._watchlist_service = watchlist_service
        self._quote_service = quote_service
        self._agent = briefing_agent
        self._session = session
        self._repo = BriefSnapshotRepository(session) if session is not None else None

    async def generate_morning_brief(self, user_id: str) -> BriefOutput:
        tickers = await self._get_watchlist_tickers(user_id)
        market_context = await self._build_market_context(tickers, phase="morning")
        logger.info("briefing.generate_morning", user_id=user_id, tickers=tickers)
        result = await self._agent.morning_brief(
            market_context=market_context,
            watchlist_tickers=tickers,
        )
        await self._persist(user_id=user_id, phase="morning", output=result, tickers=tickers)
        return result

    async def generate_eod_brief(self, user_id: str) -> BriefOutput:
        tickers = await self._get_watchlist_tickers(user_id)
        market_context = await self._build_market_context(tickers, phase="eod")
        logger.info("briefing.generate_eod", user_id=user_id, tickers=tickers)
        result = await self._agent.eod_brief(
            market_context=market_context,
            watchlist_tickers=tickers,
        )
        await self._persist(user_id=user_id, phase="eod", output=result, tickers=tickers)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _persist(
        self,
        user_id: str,
        phase: str,
        output: BriefOutput,
        tickers: list[str],
    ) -> None:
        """Save a BriefSnapshot if a session was injected. Failures are
        logged and swallowed so a DB error never blocks the brief delivery.
        """
        if self._repo is None:
            return
        try:
            content = output.summary or str(output)
            snapshot = BriefSnapshot(
                user_id=user_id,
                phase=phase,
                content=content,
                tickers=",".join(tickers) if tickers else None,
            )
            await self._repo.save(snapshot)
            logger.info(
                "briefing.snapshot_saved",
                user_id=user_id,
                phase=phase,
                snapshot_id=snapshot.id,
                ticker_count=len(tickers),
            )
        except Exception as exc:
            logger.error(
                "briefing.snapshot_save_failed",
                user_id=user_id,
                phase=phase,
                error=str(exc),
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
            # Inject tên công ty + sector từ registry — fallback silent nếu không có
            try:
                info = symbol_registry.resolve(q.ticker)
                meta = f" | {info.name} | Ngành: {info.sector}"
            except Exception:
                meta = ""
    
            volume = getattr(q, "volume", None)
            volume_text = f", volume={volume:,}" if volume is not None else ""
            lines.append(
                f"- {q.ticker}{meta}: giá={q.price:,.0f}, change={q.change:,.0f}, "
                f"change_pct={q.change_pct:.2f}%{volume_text}"
            )
        lines.append(
            "Tập trung vào mã biến động mạnh, tín hiệu risk-on/risk-off, và watchlist-specific alerts."
        )
        return "\n".join(lines)
