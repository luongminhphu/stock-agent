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
        try:
            theses = await self._thesis_repo.list_active_for_ticker(ticker, user_id)
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
            # Extract mention of this ticker from the scan summary
            lines = snapshot.summary.split(";")
            relevant = [l.strip() for l in lines if ticker in l.upper()]
            if not relevant:
                return f"Scan lúc {snapshot.scanned_at}: không có tín hiệu riêng cho {ticker}."
            return f"Scan lúc {snapshot.scanned_at}: " + "; ".join(relevant)
        except Exception as exc:
            logger.warning("pretrade_service.signal_context_error", ticker=ticker, error=str(exc))
            return ""

    async def _build_brief_context(self, ticker: str, user_id: str) -> str:
        """Extract any mention of ticker from today's latest brief.

        Best-effort: imports briefing repo lazily to avoid circular dependency.
        Returns empty string if briefing segment is not available.
        """
        try:
            from src.briefing.repository import BriefingRepository  # lazy import

            repo = BriefingRepository(self._session)
            brief = await repo.get_latest_for_user(user_id)
            if not brief:
                return ""
            # Search structured ticker_summaries first
            if brief.ticker_summaries:
                for ts in brief.ticker_summaries:
                    if ts.get("ticker", "").upper() == ticker:
                        return (
                            f"{ts.get('signal', '')} | {ts.get('one_line', '')} "
                            f"| {ts.get('change_pct', '')}%"
                        )
            # Fallback: scan plain summary text
            summary = brief.summary or ""
            if ticker in summary.upper():
                # Return a 200-char window around the first mention
                idx = summary.upper().find(ticker)
                snippet = summary[max(0, idx - 50) : idx + 150].strip()
                return snippet
            return ""
        except Exception as exc:
            logger.warning("pretrade_service.brief_context_error", ticker=ticker, error=str(exc))
            return ""
