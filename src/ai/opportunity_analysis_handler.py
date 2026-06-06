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

_SYSTEM_PROMPT = """You are an AI investment analyst for a Vietnamese stock investor.

Your task: cross-check a list of market-screened candidates against the
investor's current watchlist and active theses. Identify which candidates
are most relevant to the investor's existing positions and convictions.

Output MUST be valid JSON with this exact schema:
{
  "verdict": "string — 1 sentence summary (e.g. '2 candidates overlap with your watchlist')",
  "ranked_tickers": ["list of tickers, most relevant first, max 5"],
  "watchlist_overlap": ["tickers that appear in the investor's watchlist"],
  "thesis_relevant": ["tickers with an active thesis"],
  "action": "string — concrete next step (e.g. 'Review VHM momentum before EOD')",
  "reasoning_summary": "string — 2-3 sentences explaining the cross-check logic",
  "confidence": 0.0
}

Rules:
- Only include tickers from the provided candidate list.
- confidence: 0.0–1.0 (how confident you are in the relevance assessment).
- If no candidates are relevant to the investor's context, say so clearly in verdict.
- Keep action specific and actionable, not generic advice.
- Do not invent tickers not present in the candidates list.
"""


def _build_user_prompt(
    candidates_payload: tuple[str, ...],
    screen_criteria: str,
    watchlist_tickers: list[str],
    thesis_context: str,
    trading_date: str,
) -> str:
    """Build the user prompt for AI cross-check analysis."""
    candidates_block = "\n".join(candidates_payload) if candidates_payload else "(none)"
    watchlist_block = ", ".join(watchlist_tickers) if watchlist_tickers else "(empty)"

    return f"""Trading date: {trading_date or "today"}
Screen criteria used: {screen_criteria or "standard"}

=== MARKET SCREEN CANDIDATES (ranked by composite score) ===
{candidates_block}

=== INVESTOR WATCHLIST ===
{watchlist_block}

=== ACTIVE THESES ===
{thesis_context or "No active theses."}

Cross-check the candidates against the investor's watchlist and theses.
Return JSON as specified."""


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
        bus.subscribe(OpportunityAIAnalysisRequestedEvent, self._handle)
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
            # Step 1: Fetch investor context (watchlist + theses)
            watchlist_tickers = await self._fetch_watchlist(event.user_id)
            thesis_context = await self._fetch_thesis_context(event.user_id)

            # Step 2: AI cross-check
            user_prompt = _build_user_prompt(
                candidates_payload=event.candidates_payload,
                screen_criteria=event.screen_criteria,
                watchlist_tickers=watchlist_tickers,
                thesis_context=thesis_context,
                trading_date=event.trading_date,
            )
            raw = await self._client.complete(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                temperature=0.2,
            )

            # Step 3: Parse output
            data = _parse_output(raw)

            # Step 4: Emit completed event
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
