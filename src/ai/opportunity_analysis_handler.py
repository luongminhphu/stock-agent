"""OpportunityAnalysisHandler — ai segment, Wave 3.

Owner: ai segment.
Boundary:
  - Subscribes to OpportunityAIAnalysisRequestedEvent on the EventBus.
  - Fetches investor context: active theses (thesis segment) + watchlist
    tickers (watchlist segment) via injected query services.
  - Calls AIClient to cross-check screen candidates against investor context.
  - Emits OpportunityAnalysisCompletedEvent → bot.OpportunityAnalysisSubscriber
    for Discord delivery.
  - NEVER imports Discord, bot, or scheduler internals.
  - NEVER imports market.models or market.repository directly.

Bootstrap contract (enforced by bootstrap.py)::

    handler = OpportunityAnalysisHandler(
        ai_client=...,
        session_factory=...,
    )
    handler.register()   # idempotent

Session strategy:
    Uses session_factory (async context manager factory) — each invocation
    opens its own short-lived session. Never holds a session across the AI call.

Failure contract:
    Any error (AI timeout, DB failure, parse error) is caught and logged.
    Never raises — screen pipeline is never blocked.
    OpportunityAnalysisCompletedEvent is only emitted on success.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.platform.event_bus import get_event_bus
from src.platform.events import (
    OpportunityAIAnalysisRequestedEvent,
    OpportunityAnalysisCompletedEvent,
)
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.client import AIClient

logger = get_logger(__name__)

# ── system prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Bạn là AI phân tích đầu tư cho nhà đầu tư chứng khoán Việt Nam.

Nhiệm vụ: Cross-check danh sách cơ hội từ market screen với:
  1. Watchlist của nhà đầu tư — phát hiện overlap.
  2. Thesis đang hoạt động — xem candidate có liên quan đến luận điểm nào không.
  3. Danh mục hiện tại — phát hiện vị thế đã nắm và rủi ro tập trung ngành.

Output PHẢI là JSON hợp lệ theo schema sau:
{
  "verdict": "string — 1 câu tóm tắt bằng Tiếng Việt",
  "ranked_tickers": ["list ticker liên quan nhất, tối đa 5"],
  "watchlist_overlap": ["ticker có trong watchlist"],
  "thesis_relevant": ["ticker có thesis đang hoạt động"],
  "portfolio_overlap": ["ticker đã có vị thế mở trong danh mục"],
  "concentration_warnings": ["cảnh báo tập trung ngành nếu có, e.g. 'Thêm VCB: Banking lên 65%'"],
  "action": "string — hành động cụ thể bằng Tiếng Việt",
  "reasoning_summary": "string — 2-3 câu giải thích logic bằng Tiếng Việt",
  "confidence": 0.0
}

Quy tắc:
- Chỉ dùng ticker từ danh sách candidates cung cấp.
- confidence: 0.0–1.0.
- Nếu không có overlap hay rủi ro, nêu rõ trong verdict.
- action phải cụ thể, không chung chung.
- Toàn bộ string values phải bằng Tiếng Việt.
"""


def _build_user_prompt(
    candidates_payload: tuple[str, ...],
    screen_criteria: str,
    watchlist_tickers: list[str],
    thesis_context: str,
    trading_date: str,
    portfolio_context_text: str = "",
) -> str:
    """Build the user prompt for AI cross-check analysis."""
    candidates_block = "\n".join(candidates_payload) if candidates_payload else "(không có candidate)"
    watchlist_block = ", ".join(watchlist_tickers) if watchlist_tickers else "(trống)"

    portfolio_section = (
        f"\n=== DANH MỤC HIỆN TẠI ===\n{portfolio_context_text}"
        if portfolio_context_text
        else "\n=== DANH MỤC HIỆN TẠI ===\nChưa có vị thế nào đang mở."
    )

    return f"""Ngày giao dịch: {trading_date or "hôm nay"}
Tiêu chí screen: {screen_criteria or "tiêu chuẩn"}

=== CÁC CƠ HỘI TỪ MARKET SCREEN (xếp theo composite score) ===
{candidates_block}

=== WATCHLIST ===
{watchlist_block}

=== THESIS ĐANG HOẠT ĐỘNG ===
{thesis_context or "Không có thesis nào đang hoạt động."}
{portfolio_section}

Cross-check candidates với watchlist, thesis và danh mục hiện tại. Trả về JSON theo schema đã cho."""


def _parse_output(raw: str) -> dict[str, Any]:
    """Parse AI JSON output. Raises ValueError on failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try extracting JSON block from fenced code or prose
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Could not parse AI output as JSON: {raw[:200]}")


# ── singleton ────────────────────────────────────────────────────────────────

_instance: "OpportunityAnalysisHandler | None" = None


def get_opportunity_analysis_handler(
    ai_client: "AIClient",
    session_factory: Any,
) -> "OpportunityAnalysisHandler":
    """Return the singleton handler. Creates on first call."""
    global _instance
    if _instance is None:
        _instance = OpportunityAnalysisHandler(
            ai_client=ai_client,
            session_factory=session_factory,
        )
    return _instance


class OpportunityAnalysisHandler:
    """Subscribe to OpportunityAIAnalysisRequestedEvent → cross-check → emit result."""

    def __init__(
        self,
        ai_client: "AIClient",
        session_factory: Any,
    ) -> None:
        self._client = ai_client
        self._session_factory = session_factory

    def register(self) -> None:
        """Subscribe handler on EventBus. Safe to call multiple times."""
        bus = get_event_bus()
        bus.subscribe_handler(OpportunityAIAnalysisRequestedEvent, self._handle)
        logger.info("opportunity_analysis_handler.registered")

    async def _handle(self, event: OpportunityAIAnalysisRequestedEvent) -> None:
        """Full pipeline: fetch context → AI cross-check → emit result."""
        logger.info(
            "opportunity_analysis_handler.received",
            user_id=event.user_id,
            candidates_count=len(event.candidates_payload),
            top_symbol=event.top_symbol,
        )

        if not event.candidates_payload:
            logger.debug("opportunity_analysis_handler.no_candidates_skip")
            return

        try:
            # Step 1: Fetch investor context (watchlist + theses + portfolio)
            watchlist_tickers = await self._fetch_watchlist(event.user_id)
            thesis_context = await self._fetch_thesis_context(event.user_id)
            portfolio_context_text = await self._fetch_portfolio_context(event.user_id)

            # Step 2: AI cross-check
            user_prompt = _build_user_prompt(
                candidates_payload=event.candidates_payload,
                screen_criteria=event.screen_criteria,
                watchlist_tickers=watchlist_tickers,
                thesis_context=thesis_context,
                trading_date=event.trading_date,
                portfolio_context_text=portfolio_context_text,
            )
            api_resp = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.2,
            )
            raw = self._client.extract_text(api_resp)

            # Step 3: Parse output
            data = _parse_output(raw)

            # Step 4: Emit completed event (with portfolio cross-check fields)
            completed = OpportunityAnalysisCompletedEvent(
                user_id=event.user_id,
                verdict=str(data.get("verdict", "")),
                ranked_tickers=tuple(data.get("ranked_tickers", [])),
                watchlist_overlap=tuple(data.get("watchlist_overlap", [])),
                thesis_relevant=tuple(data.get("thesis_relevant", [])),
                action=str(data.get("action", "")),
                reasoning_summary=str(data.get("reasoning_summary", "")),
                confidence=float(data.get("confidence", 0.0)),
                trading_date=event.trading_date,
                portfolio_overlap=tuple(data.get("portfolio_overlap", [])),
                concentration_warnings=tuple(data.get("concentration_warnings", [])),
            )
            await get_event_bus().publish(completed)
            logger.info(
                "opportunity_analysis_handler.completed",
                user_id=event.user_id,
                verdict=completed.verdict,
                ranked_count=len(completed.ranked_tickers),
                watchlist_overlap=list(completed.watchlist_overlap),
                confidence=completed.confidence,
            )

        except Exception as exc:
            logger.warning(
                "opportunity_analysis_handler.failed",
                user_id=event.user_id,
                error=str(exc),
            )

    # ── private helpers ──────────────────────────────────────────────────────

    async def _fetch_portfolio_context(self, user_id: str) -> str:
        """Fetch portfolio context as formatted string for prompt injection.

        Returns PortfolioContext.format_for_prompt() — includes open positions,
        sector weights, and concentration flags. Returns empty string on failure.
        """
        try:
            from src.portfolio import get_portfolio_context

            async with self._session_factory() as session:
                ctx = await get_portfolio_context(session, user_id)
            return ctx.format_for_prompt() if ctx.has_positions else ""
        except Exception as exc:
            logger.warning(
                "opportunity_analysis_handler.portfolio_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def _fetch_watchlist(self, user_id: str) -> list[str]:
        """Fetch watchlist tickers for user. Returns [] on failure."""
        try:
            from src.watchlist.service import WatchlistService

            async with self._session_factory() as session:
                svc = WatchlistService(session)
                return await svc.get_tickers(user_id)
        except Exception as exc:
            logger.warning(
                "opportunity_analysis_handler.watchlist_fetch_failed",
                user_id=user_id,
                error=str(exc),
            )
            return []

    async def _fetch_thesis_context(self, user_id: str) -> str:
        """Fetch active theses and format as compact context string.

        Returns empty string on failure — AI prompt still works without it.
        Format mirrors TrendBatchScheduler._build_thesis_context():
          VHM: LONG | target 55,000 | stop 42,000 | "Growth momentum thesis"
        """
        try:
            from src.thesis.thesis_query_service import ThesisActiveContextQuery

            query = ThesisActiveContextQuery(session_factory=self._session_factory)
            theses = await query.get_active_with_components(user_id)
            if not theses:
                return ""

            lines: list[str] = []
            for t in theses:
                ticker = t.get("ticker", "")
                direction = t.get("direction", "LONG")
                target = t.get("target_price")
                stop = t.get("stop_loss")
                title = t.get("title", "") or t.get("summary", "")

                parts = [f"{ticker}: {direction}"]
                if target:
                    parts.append(f"target {target:,.0f}")
                if stop:
                    parts.append(f"stop {stop:,.0f}")
                if title:
                    parts.append(f'"{title[:60].strip()}"')
                lines.append(" | ".join(parts))

            return "\n".join(lines)
        except Exception as exc:
            logger.warning(
                "opportunity_analysis_handler.thesis_fetch_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""
