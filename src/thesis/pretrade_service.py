"""PreTradeService — orchestrates context gathering then calls PreTradeAgent.
Owner: thesis segment.

Responsibilities:
- Fetch active thesis for ticker (thesis segment).
- Fetch latest scan snapshot for ticker (watchlist segment via repo).
- Extract brief mention for ticker from latest briefing (briefing context).
- Call PreTradeAgent with assembled context.
- Return PreTradeCheckOutput to caller (bot command).

Does NOT own:
- Quote fetching logic (market segment).
- Scan execution (watchlist segment).
- AI prompt construction (ai segment).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.pretrade import PreTradeAgent
from src.ai.schemas import PreTradeCheckOutput
from src.market.quote_service import QuoteService
from src.platform.logging import get_logger
from src.thesis.repository import ThesisRepository
from src.watchlist.repository import WatchlistRepository

logger = get_logger(__name__)


class PreTradeService:
    """Entry point for /pretrade command."""

    def __init__(
        self,
        session: AsyncSession,
        quote_service: QuoteService,
        pretrade_agent: PreTradeAgent,
    ) -> None:
        self._session = session
        self._quote_service = quote_service
        self._agent = pretrade_agent
        self._thesis_repo = ThesisRepository(session)
        self._watchlist_repo = WatchlistRepository(session)

    async def check(self, ticker: str, user_id: str) -> PreTradeCheckOutput:
        ticker = ticker.upper().strip()
        logger.info("pretrade_service.start", ticker=ticker, user_id=user_id)

        # 1. Quote
        quote = await self._quote_service.get_quote(ticker)
        price = quote.price
        change_pct = quote.change_pct

        # 2. Thesis context
        thesis_context = await self._build_thesis_context(ticker, user_id)

        # 3. Scan signal context
        signal_context = await self._build_signal_context(ticker, user_id)

        # 4. Brief context — best-effort only, never blocks
        brief_context = await self._build_brief_context(ticker, user_id)

        # 5. AI check
        result = await self._agent.check(
            ticker=ticker,
            price=price,
            change_pct=change_pct,
            thesis_context=thesis_context,
            signal_context=signal_context,
            brief_context=brief_context,
        )
        logger.info(
            "pretrade_service.done",
            ticker=ticker,
            decision=result.decision,
            confidence=result.confidence,
        )
        return result

    # ------------------------------------------------------------------
    # Context builders — each is best-effort, returns empty string on miss
    # ------------------------------------------------------------------

    async def _build_thesis_context(self, ticker: str, user_id: str) -> str:
        """Return thesis context for ticker scoped to this user.

        ThesisRepository.list_active_by_ticker() returns all users' theses
        for a ticker, so we filter by user_id here.
        """
        try:
            all_theses = await self._thesis_repo.list_active_by_ticker(ticker)
            theses = [t for t in all_theses if t.user_id == user_id]
            if not theses:
                return ""
            parts = []
            for t in theses[:2]:  # top 2 active theses
                parts.append(
                    f"Thesis: {t.title}\n"
                    f"Summary: {t.summary or ''}\n"
                    f"Entry: {t.entry_price or 'N/A'} | Target: {t.target_price or 'N/A'} "
                    f"| Stop: {t.stop_loss or 'N/A'}\n"
                    f"Score: {t.score or 'N/A'}"
                )
            return "\n\n".join(parts)
        except Exception as exc:
            logger.warning("pretrade_service.thesis_context_error", ticker=ticker, error=str(exc))
            return ""

    async def _build_signal_context(self, ticker: str, user_id: str) -> str:
        try:
            snapshot = await self._watchlist_repo.get_latest_scan(user_id)
            if not snapshot or not snapshot.summary:
                return ""
            lines = snapshot.summary.split(";")
            relevant = [line.strip() for line in lines if ticker in line.upper()]
            if not relevant:
                return f"Scan lúc {snapshot.scanned_at}: không có tín hiệu riêng cho {ticker}."
            return f"Scan lúc {snapshot.scanned_at}: " + "; ".join(relevant)
        except Exception as exc:
            logger.warning("pretrade_service.signal_context_error", ticker=ticker, error=str(exc))
            return ""

    async def _build_brief_context(self, ticker: str, user_id: str) -> str:
        """Extract any mention of ticker from today's latest brief.

        BriefSnapshot only stores plain `content` (Markdown text).
        We search for ticker in content and return a 200-char snippet.
        Tries morning brief first, falls back to EOD.
        """
        try:
            from src.briefing.repository import BriefSnapshotRepository  # lazy to avoid circular

            repo = BriefSnapshotRepository(self._session)
            brief = await repo.get_latest(user_id, "morning") or await repo.get_latest(
                user_id, "eod"
            )
            if not brief or not brief.content:
                return ""
            content_upper = brief.content.upper()
            if ticker not in content_upper:
                return ""
            idx = content_upper.find(ticker)
            snippet = brief.content[max(0, idx - 50) : idx + 150].strip()
            return f"[Brief {brief.phase} {brief.created_at.date()}] ...{snippet}..."
        except Exception as exc:
            logger.warning("pretrade_service.brief_context_error", ticker=ticker, error=str(exc))
            return ""
