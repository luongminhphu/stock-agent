"""ThesisJudgeAgent — automated thesis × signal cross-check.

Owner: ai segment.
Trigger: SignalEngineOutput.thesis_review_triggers (emitted by SignalEngineAgent).
Caller: BriefingService — runs batch after SignalEngine, before BriefingAgent LLM call.

Distinct from ThesisReviewAgent:
  - ThesisReviewAgent: user-initiated, full context, deep analysis, writes to UI.
  - ThesisJudgeAgent:  auto-triggered, fast cross-check, feeds briefing + readmodel.

Responsibility:
  - Receives signal_context (watchdog/stress verdict) + thesis metadata.
  - Produces ThesisJudgeOutput per thesis: verdict, conviction_delta,
    challenged_assumptions, new_risks, action, reasoning.
  - run_batch(): processes multiple triggered theses concurrently.
  - Does NOT write to DB — caller (BriefingService) owns persistence decision.
  - Does NOT trigger ThesisReviewAgent — only signals action="review" for caller.

Boundary:
  - ONLY reads thesis metadata passed in — no DB calls.
  - ONLY uses signal_context passed in — no market API calls.
  - bot and api NEVER call this directly — only through BriefingService.

Memory logging (Wave 6):
  - run() and run_batch() accept optional session + user_id params.
  - When provided, every verdict (AI or fallback) is logged as an episodic entry.
  - caller (BriefingService) passes its own session — agent never opens DB directly.
  - Backward-compat: session=None skips logging silently.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Required, TypedDict

from src.ai.client import AIClient, AIError
from src.ai.prompts.thesis_judge import SPEC, build_user_prompt
from src.ai.schemas import (
    ChallengedAssumption,
    ThesisJudgeOutput,
    ThesisJudgeVerdict,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Maximum number of concurrent LLM calls in run_batch().
# Prevents rate-limit cascade on large watchlists where all theses would
# otherwise fire simultaneously via asyncio.gather. Each thesis that hits
# a rate limit falls back gracefully to rule-based output independently.
_JUDGE_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Input contract (Issue J)
# ---------------------------------------------------------------------------

class ThesisJudgeTrigger(TypedDict, total=False):
    """Typed input for run_batch(). Required keys: thesis_id, ticker.

    Passing a dict missing thesis_id or ticker will be caught at runtime
    (KeyError in run_batch) rather than silently producing thesis_id='unknown'
    downstream in readmodel / briefing.

    signal_context expected keys (see prompts/thesis_judge.py SignalContext):
        watchdog_verdict, urgency, trigger_reason, risk_flags,
        health_score, stress_verdict, signal_summary, last_review_summary.
    """
    thesis_id: Required[str | int]
    ticker: Required[str]
    thesis_title: str
    thesis_summary: str
    assumptions: list[dict[str, Any]]
    catalysts: list[dict[str, Any]]
    invalidation_conditions: list[str]
    signal_context: dict[str, Any]
    conviction_history: list[dict[str, Any]] | None
    days_since_written: int | None
    last_reviewed_at: str | None   # ISO 8601 — from thesis.last_reviewed_at
    last_judged_at: str | None     # ISO 8601 — from previous ThesisJudgeOutput.judged_at


# ---------------------------------------------------------------------------
# Fallback helpers
# ---------------------------------------------------------------------------


def _derive_fallback_verdict(
    watchdog_verdict: str | None,
    signal_urgency: str | None,
) -> tuple[ThesisJudgeVerdict, float, str]:
    """Rule-based verdict when AI is unavailable.

    Issue L fix: normalise inputs to uppercase before comparison to handle
    callers that pass title-case or lowercase values (e.g. 'Bearish', 'high').

    Returns: (verdict, conviction_delta, action)
    """
    verdict_upper = (watchdog_verdict or "").upper()
    urgency_upper = (signal_urgency or "").upper()

    is_bearish = verdict_upper == "BEARISH"
    is_critical = urgency_upper == "CRITICAL"
    is_high = urgency_upper == "HIGH"

    if is_bearish and is_critical:
        return ThesisJudgeVerdict.INVALIDATED, -0.6, "exit_signal"
    if is_bearish or is_critical:
        return ThesisJudgeVerdict.WEAKENING, -0.35, "review"
    if is_high:
        return ThesisJudgeVerdict.WEAKENING, -0.2, "reduce"
    return ThesisJudgeVerdict.ON_TRACK, 0.0, "hold"


# ---------------------------------------------------------------------------
# Batch dedup helpers
# ---------------------------------------------------------------------------

_DEDUP_WINDOW_SECONDS = 1800  # 30 minutes


def _parse_iso_dt(ts: str | None) -> datetime | None:
    """Parse ISO 8601 string to UTC-aware datetime. Returns None on failure."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _dedup_triggers(
    triggers: list[ThesisJudgeTrigger],
    window_seconds: int = _DEDUP_WINDOW_SECONDS,
) -> tuple[list[ThesisJudgeTrigger], list[ThesisJudgeTrigger]]:
    """Split triggers into (active, skipped) based on recency + signal presence.

    Skip rule: skip a trigger when ALL of the following are true:
      1. last_reviewed_at OR last_judged_at is within window_seconds of now.
      2. signal_context is empty (no new event since last review).

    Rationale:
      - If there's a non-empty signal_context, BriefingService found a real
        new event — always re-judge.
      - If the thesis was judged/reviewed very recently (< 30 min) with no
        new signal, re-calling AI returns the same answer at token cost.

    Args:
        triggers:       Full list from run_batch caller.
        window_seconds: Freshness window. Default = 1800 (30 min).

    Returns:
        Tuple of (active_triggers, skipped_triggers).
        active: will be sent to AI.
        skipped: will receive rule-based fallback.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_seconds)

    active: list[ThesisJudgeTrigger] = []
    skipped: list[ThesisJudgeTrigger] = []

    for t in triggers:
        signal_context = t.get("signal_context", {})
        has_new_signal = bool(signal_context)  # non-empty = new event present

        if has_new_signal:
            # New signal — always judge
            active.append(t)
            continue

        # Check recency: use the more recent of last_reviewed_at / last_judged_at
        ts_reviewed = _parse_iso_dt(t.get("last_reviewed_at"))
        ts_judged = _parse_iso_dt(t.get("last_judged_at"))

        # Take the most recent timestamp available
        candidates = [ts for ts in (ts_reviewed, ts_judged) if ts is not None]
        most_recent = max(candidates) if candidates else None

        if most_recent is not None and most_recent >= cutoff:
            # Recently reviewed/judged, no new signal — skip
            skipped.append(t)
        else:
            # No recent review OR stale (> 30 min) — always judge
            active.append(t)

    return active, skipped


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ThesisJudgeAgent:
    """Runs fast thesis × signal cross-check for auto-triggered review.

    Intended call site: BriefingService, after SignalEngineAgent.run(),
    before BriefingAgent LLM call.

    Example usage::

        judge = ThesisJudgeAgent(ai_client)

        trigger_inputs: list[ThesisJudgeTrigger] = [
            {
                "thesis_id": "42",
                "ticker": "VHM",
                "thesis_title": "VHM phục hồi sau chu kỳ margin call",
                "thesis_summary": "...",
                "assumptions": [{"id": 1, "description": "Lãi suất giảm Q3"}],
                "catalysts": [{"id": 3, "description": "KQKD Q2 > kỳ vọng"}],
                "invalidation_conditions": ["Margin call lần 2", "P/B vượt 2.0x"],
                "signal_context": {
                    "watchdog_verdict": "BEARISH",
                    "urgency": "HIGH",
                    "trigger_reason": "Dòng tiền khối ngoại bán ròng 3 phiên",
                    "risk_flags": ["volume_spike", "foreign_sell"],
                    "last_review_summary": "NEUTRAL — thesis còn hợp lệ, chờ KQKD Q2",
                },
            }
        ]

        results = await judge.run_batch(trigger_inputs)
        # results: list[ThesisJudgeOutput] — inject into BriefingAgent context
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def run(
        self,
        *,
        thesis_id: str | int,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions: list[dict[str, Any]],
        catalysts: list[dict[str, Any]],
        invalidation_conditions: list[str],
        signal_context: dict[str, Any],
        conviction_history: list[dict[str, Any]] | None = None,
        days_since_written: int | None = None,
        session: Any = None,
        user_id: str | None = None,
        investor_context: str = "",
    ) -> ThesisJudgeOutput:
        """Run a single thesis judge check. Returns ThesisJudgeOutput.

        Fallback: if AI call fails, returns rule-based output derived from
        watchdog_verdict + signal_urgency. Confidence=0.3 signals degraded quality.

        Error classification (Issue K):
          - Rate limit / network timeout → INFO log (expected operational noise).
          - JSON parse / schema validation error → ERROR log (possible prompt regression).
          - Other AIError → WARNING log.
          All three paths fall back to rule-based output without re-raising.

        Args:
            thesis_id:               Thesis ID for traceability.
            ticker:                  Mã cổ phiếu.
            thesis_title:            Tiêu đề thesis.
            thesis_summary:          Tóm tắt luận điểm.
            assumptions:             Active assumptions [{"id", "description", "status"}].
            catalysts:               Pending catalysts [{"id", "description", "status"}].
            invalidation_conditions: Explicit conditions that would kill the thesis.
            signal_context:          Signal data from SignalEngine / Watchdog output.
                                     Expected keys: watchdog_verdict, urgency,
                                     trigger_reason, risk_flags, health_score,
                                     stress_verdict, signal_summary, last_review_summary.
            conviction_history:      Last N judge verdicts for trend context.
            days_since_written:      Days since thesis was created.
            session:                 Optional DB session from caller (BriefingService).
                                     When provided, verdict is logged as episodic memory.
            user_id:                 Optional user ID for episodic memory logging.
            investor_context:        Pre-rendered investor profile + memory block from
                                     ContextBuilder.render_for_agent(). Injected into
                                     prompt so judge is aware of investor bias patterns.
                                     Empty string → skipped (backward-compat).
        """
        import json

        from pydantic import ValidationError

        user_prompt = build_user_prompt(
            thesis_id=thesis_id,
            ticker=ticker,
            thesis_title=thesis_title,
            thesis_summary=thesis_summary,
            assumptions=assumptions,
            catalysts=catalysts,
            invalidation_conditions=invalidation_conditions,
            signal_context=signal_context,
            conviction_history=conviction_history,
            days_since_written=days_since_written,
            investor_context=investor_context,
        )

        try:
            result: ThesisJudgeOutput = await self._client.structured_call(
                spec=SPEC,
                user_prompt=user_prompt,
            )
            # Stamp thesis_id on result for downstream consumers
            result.thesis_id = str(thesis_id)
            result.ticker = ticker
            result.judged_at = datetime.now(UTC).isoformat()

            logger.info(
                "ThesisJudge: thesis=%s ticker=%s verdict=%s delta=%+.2f action=%s",
                thesis_id,
                ticker,
                result.verdict,
                result.conviction_delta,
                result.action,
            )
            await _log_thesis_judge_interaction(session, user_id, result)
            return result

        # Issue K: split error log levels by error type.
        # Rate limit is operational noise → INFO. Parse errors may indicate
        # prompt regression → ERROR so alerts fire. Other AI errors → WARNING.
        except AIError as exc:
            exc_type = type(exc).__name__
            # Detect rate limit by class name or message heuristic — avoids
            # hard dependency on a specific AIRateLimitError subclass that may
            # not exist in all client implementations.
            is_rate_limit = "rate" in exc_type.lower() or "ratelimit" in exc_type.lower()
            if is_rate_limit:
                logger.info(
                    "ThesisJudge: rate limit for thesis=%s ticker=%s, using fallback",
                    thesis_id, ticker,
                )
            else:
                logger.warning(
                    "ThesisJudge: AI error for thesis=%s ticker=%s: %s",
                    thesis_id, ticker, exc,
                )
            fallback = self._fallback(
                thesis_id=thesis_id,
                ticker=ticker,
                signal_context=signal_context,
            )
            await _log_thesis_judge_interaction(session, user_id, fallback)
            return fallback

        except (json.JSONDecodeError, ValidationError) as exc:
            # Parse / schema errors may indicate prompt regression — log at ERROR
            # so monitoring alerts can catch systematic failures.
            logger.error(
                "ThesisJudge: parse error for thesis=%s ticker=%s "
                "— possible prompt regression: %s",
                thesis_id, ticker, exc,
            )
            fallback = self._fallback(
                thesis_id=thesis_id,
                ticker=ticker,
                signal_context=signal_context,
            )
            await _log_thesis_judge_interaction(session, user_id, fallback)
            return fallback

        except Exception as exc:
            logger.warning(
                "ThesisJudgeAgent unexpected error for thesis=%s ticker=%s: %s",
                thesis_id, ticker, exc,
            )
            fallback = self._fallback(
                thesis_id=thesis_id,
                ticker=ticker,
                signal_context=signal_context,
            )
            await _log_thesis_judge_interaction(session, user_id, fallback)
            return fallback

    async def run_batch(
        self,
        triggers: list[ThesisJudgeTrigger],
        session: Any = None,
        user_id: str | None = None,
        investor_context: str = "",
    ) -> list[ThesisJudgeOutput]:
        """Run thesis judge for multiple triggers concurrently.

        Accepts list[ThesisJudgeTrigger] (TypedDict) — plain dicts are also
        accepted at runtime since TypedDict is a dict subtype. Required keys
        thesis_id and ticker are accessed via [] (KeyError on missing) to
        prevent silent thesis_id='unknown' propagating to readmodel/briefing.

        Per-thesis errors are caught and replaced with fallback output —
        one failure never blocks the rest of the batch.

        Concurrency is capped at _JUDGE_CONCURRENCY (default 5) via asyncio.Semaphore
        to prevent rate-limit cascades on large watchlists.

        Args:
            triggers: list[ThesisJudgeTrigger] — each entry must have
                thesis_id (Required) and ticker (Required). Optional keys:
                thesis_title, thesis_summary, assumptions, catalysts,
                invalidation_conditions, signal_context,
                conviction_history, days_since_written.
            session:  Optional DB session from caller (BriefingService).
                      When provided, every verdict is logged as episodic memory.
            user_id:  Optional user ID for episodic memory logging.

        Returns:
            list[ThesisJudgeOutput] in same order as triggers.
        """
        if not triggers:
            return []

        # Dedup: skip re-judging theses reviewed/judged recently with no new signal.
        # A non-empty signal_context means BriefingService found a new trigger event
        # for this thesis — always re-judge in that case.
        active_triggers, skipped = _dedup_triggers(triggers)
        if skipped:
            logger.info(
                "ThesisJudge batch: skipped %d/%d triggers (reviewed < 30 min, no new signal)",
                len(skipped),
                len(triggers),
            )

        # Build a lookup for skipped theses so we can return fallback for them
        # using rule-based output instead of calling AI.
        skipped_outputs = [
            self._fallback(
                thesis_id=t["thesis_id"],
                ticker=t["ticker"],
                signal_context=t.get("signal_context", {}),
            )
            for t in skipped
        ]

        # Map thesis_id → position in original triggers list for result ordering
        id_to_idx = {t["thesis_id"]: i for i, t in enumerate(triggers)}

        sem = asyncio.Semaphore(_JUDGE_CONCURRENCY)

        async def _run_one(t: ThesisJudgeTrigger) -> ThesisJudgeOutput:
            async with sem:
                try:
                    return await self.run(
                        thesis_id=t["thesis_id"],
                        ticker=t["ticker"],
                        thesis_title=t.get("thesis_title", ""),
                        thesis_summary=t.get("thesis_summary", ""),
                        assumptions=t.get("assumptions", []),
                        catalysts=t.get("catalysts", []),
                        invalidation_conditions=t.get("invalidation_conditions", []),
                        signal_context=t.get("signal_context", {}),
                        conviction_history=t.get("conviction_history"),
                        days_since_written=t.get("days_since_written"),
                        session=session,
                        user_id=user_id,
                        investor_context=investor_context,
                    )
                except Exception as exc:
                    # Should not reach here (run() has its own try/except),
                    # but guard at batch level for absolute safety.
                    logger.error(
                        "ThesisJudge batch: unexpected error for thesis=%s ticker=%s: %s",
                        t.get("thesis_id", "?"),
                        t.get("ticker", "?"),
                        exc,
                    )
                    return self._fallback(
                        thesis_id=t.get("thesis_id", "unknown"),
                        ticker=t.get("ticker", ""),
                        signal_context=t.get("signal_context", {}),
                    )

        active_results = await asyncio.gather(*[_run_one(t) for t in active_triggers])

        # Merge active + skipped results back in original trigger order.
        # Build a dict keyed by thesis_id for O(1) lookup.
        result_map: dict[Any, ThesisJudgeOutput] = {}
        for t, r in zip(active_triggers, active_results):
            result_map[t["thesis_id"]] = r
        for t, r in zip(skipped, skipped_outputs):
            result_map[t["thesis_id"]] = r

        ordered = [result_map[t["thesis_id"]] for t in triggers]

        logger.info(
            "ThesisJudge batch complete: %d AI calls, %d skipped (dedup), %d total",
            len(active_results),
            len(skipped_outputs),
            len(ordered),
        )
        return ordered

    def _fallback(
        self,
        *,
        thesis_id: str | int,
        ticker: str,
        signal_context: dict[str, Any],
    ) -> ThesisJudgeOutput:
        """Rule-based fallback when AI is unavailable.

        Derives verdict from watchdog_verdict + signal_urgency (normalised
        to uppercase — see _derive_fallback_verdict).
        Confidence=0.3 signals degraded quality to downstream consumers.
        challenged_assumptions is empty — cannot determine without AI.

        Note on judged_at timing: this timestamp reflects when the fallback
        was invoked, which may be after an AI timeout. For debugging, compare
        with the original trigger timestamp in signal_context.
        """
        watchdog_verdict = signal_context.get("watchdog_verdict")
        signal_urgency = signal_context.get("urgency")
        trigger_reason = signal_context.get("trigger_reason", "AI unavailable — rule-based fallback")

        verdict, conviction_delta, action = _derive_fallback_verdict(
            watchdog_verdict=watchdog_verdict,
            signal_urgency=signal_urgency,
        )

        return ThesisJudgeOutput(
            thesis_id=str(thesis_id),
            ticker=ticker,
            verdict=verdict,
            conviction_delta=conviction_delta,
            challenged_assumptions=[],  # cannot determine without AI
            new_risks=(
                [trigger_reason]
                if verdict in (ThesisJudgeVerdict.WEAKENING, ThesisJudgeVerdict.INVALIDATED)
                else []
            ),
            action=action,
            reasoning=f"Rule-based fallback — AI unavailable. Derived from: "
                       f"watchdog={watchdog_verdict}, urgency={signal_urgency}.",
            confidence=0.3,
            judged_at=datetime.now(UTC).isoformat(),
        )


    async def judge(
        self,
        theses: list[Any],
        session: Any = None,
        user_id: str | None = None,
        investor_context: str = "",
    ) -> str:
        """Convenience wrapper for BriefingService: batch-judge active theses.

        Converts thesis domain objects to ThesisJudgeTrigger dicts,
        runs run_batch(), and renders a compact summary string for briefing.

        Args:
            theses:           list of Thesis domain objects from ThesisService.
            session:          Optional DB session for memory logging.
            user_id:          Optional user ID for memory logging.
            investor_context: Pre-rendered ContextBuilder block (profile + memory).

        Returns:
            Compact multi-line verdict string or empty string.
            Never raises.
        """
        if not theses:
            return ""
        try:
            triggers: list[ThesisJudgeTrigger] = [
                {
                    "thesis_id": str(getattr(t, "id", "?")),
                    "ticker":     getattr(t, "ticker", ""),
                    "thesis_title":   getattr(t, "title", ""),
                    "thesis_summary": getattr(t, "summary", "") or "",
                    "signal_context": {},
                    # Populate timestamp fields for Wave 4 dedup guard.
                    # last_reviewed_at: from Thesis.last_reviewed_at (persisted column,
                    #   migration 0041). Falls back gracefully to None when column not yet
                    #   present (older DB) — dedup guard treats None as stale = always judge.
                    "last_reviewed_at": (
                        getattr(t, "last_reviewed_at", None).isoformat()
                        if getattr(t, "last_reviewed_at", None) is not None
                        else None
                    ),
                    # last_judged_at: from Thesis.last_judged_at (persisted column,
                    #   migration 0041, written by _log_thesis_judge_interaction).
                    "last_judged_at": (
                        getattr(t, "last_judged_at", None).isoformat()
                        if getattr(t, "last_judged_at", None) is not None
                        else None
                    ),
                }
                for t in theses
            ]
            results = await self.run_batch(
                triggers,
                session=session,
                user_id=user_id,
                investor_context=investor_context,
            )
            lines = [
                f"{r.ticker}: {r.verdict} (delta={r.conviction_delta:+.2f}, action={r.action})"
                for r in results
            ]
            return "\n".join(lines) if lines else ""
        except Exception as exc:
            logger.warning("ThesisJudge.judge wrapper failed: %s", exc)
            return ""


# ---------------------------------------------------------------------------
# Memory interaction loggers — module-level helpers (Wave 6)
# ---------------------------------------------------------------------------


async def _log_thesis_judge_interaction(
    session: Any,
    user_id: str | None,
    result: ThesisJudgeOutput,
) -> None:
    """Fire-and-forget memory log for a single thesis judge verdict.

    Caller (BriefingService) passes its own session — thesis_judge never
    opens a DB session directly (boundary: ai segment, no DB access).

    Judge verdict + conviction_delta are the xương sống (backbone) of semantic
    synthesis: they capture whether the investor's conviction is being reinforced
    or eroded by real-time signals — exactly the pattern to accumulate over time.

    Logs every verdict including fallback (confidence=0.3) so the memory layer
    can track AI-availability trends as a meta-signal.

    Never raises. Silently skips when session is None or user_id unset.
    """
    if session is None or not user_id:
        return
    try:
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        ticker = getattr(result, "ticker", "") or ""
        verdict = str(getattr(result, "verdict", "") or "")
        delta = getattr(result, "conviction_delta", 0.0) or 0.0
        action = str(getattr(result, "action", "") or "")
        confidence = getattr(result, "confidence", 0.0) or 0.0
        challenged = getattr(result, "challenged_assumptions", []) or []

        entry = InteractionEntry(
            user_id=user_id,
            agent_type="thesis_judge",
            trigger="signal_triggered_judge",
            tickers=[ticker] if ticker else [],
            ai_verdict=verdict,
            ai_key_points=(
                f"conviction_delta={delta:+.2f} "
                f"action={action} "
                f"confidence={confidence:.2f} "
                f"challenged_assumptions={len(challenged)}"
            ),
        )
        await MemoryService.log_interaction(session, entry)

        # Persist last_judged_at on the Thesis row so Wave 4 dedup guard
        # can read it without relying on in-memory ThesisJudgeOutput.judged_at
        # (which is lost on process restart).
        thesis_id_val = getattr(result, "thesis_id", None)
        if thesis_id_val and str(thesis_id_val).isdigit():
            try:
                from sqlalchemy import text as _text
                await session.execute(
                    _text(
                        "UPDATE theses SET last_judged_at = :ts "
                        "WHERE id = :thesis_id"
                    ),
                    {"ts": datetime.now(UTC), "thesis_id": int(thesis_id_val)},
                )
            except Exception as db_exc:
                logger.warning(
                    "thesis_judge.last_judged_at_update_failed",
                    thesis_id=thesis_id_val,
                    error=str(db_exc),
                )
    except Exception as exc:
        logger.warning("thesis_judge.memory_log_failed", error=str(exc))


async def _log_thesis_judge_batch_interaction(
    session: Any,
    user_id: str | None,
    results: list[ThesisJudgeOutput],
) -> None:
    """Log memory entries for a full judge batch concurrently. Never raises.

    Thin wrapper over _log_thesis_judge_interaction — gathers all entries
    concurrently so a large batch doesn't serialize memory writes.
    return_exceptions=True ensures one failure never blocks the rest.
    """
    if session is None or not user_id or not results:
        return
    await asyncio.gather(
        *[_log_thesis_judge_interaction(session, user_id, r) for r in results],
        return_exceptions=True,
    )
