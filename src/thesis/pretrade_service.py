"""PreTradeService — orchestrates context gathering then calls PreTradeAgent.
Owner: thesis segment.

Responsibilities:
- Fetch active thesis for ticker (thesis segment).
- Fetch latest scan snapshot for ticker (watchlist segment via repo).
- Extract brief mention for ticker from latest briefing (briefing context).
- Fetch past evaluated decisions for user (thesis segment via LessonService).
- Fetch general market regime (market segment, read-only via injected
  MarketRegimeService — Wave 8.1: never fight the general market).
- Deterministically override the AI verdict when position sizing detects an
  averaging-down entry (portfolio segment, Wave 8.2 — Livermore: don't
  rescue a losing position by buying more of it). This is enforced in code,
  not left to the LLM, because sizing is only known after the AI already
  answered.
- Call PreTradeAgent with assembled context + session for investor profile.
- Return PreTradeCheckOutput to caller (bot command).

Does NOT own:
- Quote fetching logic (market segment).
- Scan execution (watchlist segment).
- AI prompt construction (ai segment).
- Investor profile assembly (ai.ContextBuilder).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.pretrade import PreTradeAgent
from src.ai.schemas import PreTradeCheckOutput, TradeDecision
from src.market.quote_service import QuoteService
from src.platform.logging import get_logger
from src.thesis.lesson_service import LessonService
from src.thesis.repository import ThesisRepository
from src.watchlist.repository import WatchlistRepository

if TYPE_CHECKING:
    from src.portfolio.position_sizing_service import SizingResult

logger = get_logger(__name__)


class PreTradeService:
    """Entry point for /pretrade command."""

    def __init__(
        self,
        session: AsyncSession,
        quote_service: QuoteService,
        pretrade_agent: PreTradeAgent,
        market_regime_service: object | None = None,
    ) -> None:
        self._session = session
        self._quote_service = quote_service
        self._agent = pretrade_agent
        # Optional (Wave 8.1) — None keeps existing callers backward-compatible;
        # market context block is simply omitted (empty string) when not wired.
        self._market_regime_service = market_regime_service
        self._thesis_repo = ThesisRepository(session)
        self._watchlist_repo = WatchlistRepository(session)
        self._lesson_service = LessonService(session)

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

        # 5. Past lessons — ticker-specific, best-effort, never blocks
        past_lessons = await self._build_lesson_context(ticker, user_id)

        # 5b. Market regime — general market gate (Wave 8.1), best-effort, never blocks
        market_context = await self._build_market_context()

        # 6. AI check — session forwarded so ContextBuilder can inject investor profile
        result = await self._agent.check(
            ticker=ticker,
            price=price,
            change_pct=change_pct,
            thesis_context=thesis_context,
            signal_context=signal_context,
            brief_context=brief_context,
            past_lessons=past_lessons,
            market_context=market_context,
            session=self._session,
        )
        # 6b. Quantitative sizing gate (Wave 2) — portfolio segment owns the
        # math; thesis segment attaches the result to the advisory output.
        # Best-effort: sizing failure never blocks the pre-trade answer.
        sizing_result = await self._compute_sizing(ticker=ticker, user_id=user_id, price=price)
        if sizing_result is not None:
            result.sizing_note = sizing_result.to_note()

            # 6c. Averaging-down hard gate (Wave 8.2 — Livermore rule 2).
            # Deterministic override, NOT a prompt rule: sizing runs AFTER the
            # AI verdict above, so the AI never sees this signal beforehand.
            # A hard risk invariant like "don't rescue a loser" must not
            # depend on the LLM noticing it after the fact — thesis segment
            # enforces it directly. intended_action=SELL is this codebase's
            # existing convention for the "AVOID" verdict on a prospective
            # entry (see bot/commands/pretrade.py _DECISION_META).
            if (
                sizing_result.cap_reason == "averaging_down_blocked"
                and result.intended_action == TradeDecision.BUY
            ):
                result.intended_action = TradeDecision.SELL
                result.proceed_recommendation = False
                result.blocking_issues.append(
                    "Bình quân giá xuống bị chặn: giá hiện tại thấp hơn giá vốn "
                    "trung bình của vị thế đang mở (Livermore: không mua thêm để "
                    "cứu một vị thế đang thua)."
                )
                logger.info(
                    "pretrade_service.averaging_down_override",
                    ticker=ticker, user_id=user_id,
                )

        # 7. Persist the advice for later reconciliation (Wave 1).
        # Best-effort: persistence failure must never block the user-facing
        # answer. Uses flush (not commit) so the caller's transaction boundary
        # is preserved.
        await self._persist_advice(ticker=ticker, user_id=user_id, price=price, result=result)

        logger.info(
            "pretrade_service.done",
            ticker=ticker,
            decision=result.intended_action,
            confidence=result.confidence,
            has_lessons=bool(past_lessons),
            has_market_context=bool(market_context),
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

    async def _build_market_context(self) -> str:
        """Return MarketRegime.format_for_prompt() string (Wave 8.1).

        Read-only from market segment via injected MarketRegimeService.
        Returns empty string when the service was not wired (None) or on any
        fetch error — pretrade must never be blocked by market data.
        """
        if self._market_regime_service is None:
            return ""
        try:
            regime = await self._market_regime_service.get_regime()
            return regime.format_for_prompt()
        except Exception as exc:
            logger.warning("pretrade_service.market_context_error", error=str(exc))
            return ""

    async def _compute_sizing(
        self, *, ticker: str, user_id: str, price: float
    ) -> "SizingResult | None":
        """Quantitative position sizing from PositionSizingService. Never raises.

        Returns the raw SizingResult (not just its rendered note) so callers
        can inspect cap_reason — e.g. the averaging-down hard gate (Wave 8.2)
        needs the structured reason, not just display text. Overrides the
        LLM-written sizing_note with hard numbers computed by Python
        (fixed-fractional risk model); the LLM narrative remains in the
        persisted rationale.
        """
        try:
            from src.portfolio.position_sizing_service import PositionSizingService

            svc = PositionSizingService(self._session)
            return await svc.size_for_entry(
                user_id=user_id, ticker=ticker, entry_price=price
            )
        except Exception as exc:
            logger.warning(
                "pretrade_service.sizing_failed",
                ticker=ticker,
                user_id=user_id,
                error=str(exc),
            )
            return None

    async def _persist_advice(
        self,
        *,
        ticker: str,
        user_id: str,
        price: float,
        result: PreTradeCheckOutput,
    ) -> None:
        """Write the AI verdict to DecisionLog (PRETRADE_ADVICE). Never raises."""
        try:
            from src.thesis.decision_service import DecisionService

            rationale_parts = [
                f"verdict={result.verdict} action={result.intended_action} "
                f"confidence={result.confidence:.2f} proceed={result.proceed_recommendation}",
                result.summary,
            ]
            if result.blocking_issues:
                rationale_parts.append(
                    "blocking: " + "; ".join(result.blocking_issues[:3])
                )

            svc = DecisionService(self._session, quote_service=self._quote_service)
            await svc.log_pretrade_advice(
                user_id=user_id,
                ticker=ticker,
                verdict=str(result.verdict),
                confidence=result.confidence,
                rationale="\n".join(p for p in rationale_parts if p),
                price_at_decision=price,
            )
        except Exception as exc:
            logger.warning(
                "pretrade_service.persist_advice_failed",
                ticker=ticker,
                user_id=user_id,
                error=str(exc),
            )

    async def _build_lesson_context(self, ticker: str, user_id: str) -> str:
        """Fetch ticker-specific past decision lessons for AI personalisation.

        Uses LessonService with ticker filter so only decisions on this exact
        ticker are included — keeps the pretrade prompt focused.

        Returns empty string if no evaluated decisions exist for this ticker
        yet, or on any error — pretrade must never be blocked by lesson data.
        """
        try:
            return await self._lesson_service.build_lesson_context(
                user_id=user_id,
                ticker=ticker,
                limit=3,  # pretrade: tighter context window than morning brief
            )
        except Exception as exc:
            logger.warning("pretrade_service.lesson_context_error", ticker=ticker, error=str(exc))
            return ""
