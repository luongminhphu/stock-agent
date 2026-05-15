"""ThesisReviewAgent — AI agent for reviewing a single investment thesis.

Owner: ai segment.
Caller: thesis.review_service — passes in all domain data,
receives typed ThesisReviewOutput back.

This agent does NOT know thesis business rules (invalidation thresholds,
scoring weights). Those live in the thesis segment.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import ValidationError

from src.ai.client import AIClient, AIError
from src.ai.prompts.thesis_review import SYSTEM_PROMPT, build_review_prompt
from src.ai.schemas import ThesisReviewOutput
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Matches ```json ... ``` or ``` ... ``` fences
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

# Max tokens for thesis review — responses include reasoning + multiple
# recommendations and regularly exceed 1200 tokens. 4096 gives headroom.
_MAX_TOKENS = 4096


def _extract_json(text: str) -> str:
    """Extract JSON object from text, handling markdown fences and extra prose.

    Strategy (in order):
    1. Strip markdown code fence via regex — handles well-formed ```json...```.
    2. Brace-scan fallback — find first '{' and last '}' in the string.
       Handles: truncated fences (no closing ```), raw JSON with surrounding
       prose, and AI responses where fence regex doesn't match.
    """
    text = text.strip()

    match = _JSON_FENCE_RE.search(text)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    return text


def _extract_previous_review(
    episodes: list,
    thesis_id: int | None,
) -> dict | None:
    """Extract the most recent thesis_review episode as a previous verdict anchor.

    Scans the already-filtered episode list (scoped to ticker + thesis_id by
    _fetch_memory_for_review_full) for the latest thesis_review agent entry.
    No additional DB call — reuses data already in memory.

    Returns a dict with keys:
        reviewed_at  (str, YYYY-MM-DD)
        verdict      (str, e.g. BULLISH)
        confidence   (float | None)
        summary      (str, first line of ai_key_points or empty)
        key_risks    (list[str], from ai_risk_signals lines, up to 3)
    Returns None if no previous thesis_review episode exists.
    """
    thesis_episodes = [
        ep for ep in episodes
        if ep.agent_type == "thesis_review"
        and (thesis_id is None or ep.thesis_id == thesis_id)
    ]
    if not thesis_episodes:
        return None

    # Episodes are already sorted newest-first by MemoryService
    latest = thesis_episodes[0]
    return {
        "reviewed_at": latest.created_at.strftime("%Y-%m-%d"),
        "verdict": latest.ai_verdict or "N/A",
        "confidence": latest.ai_confidence,
        "summary": (
            latest.ai_key_points.splitlines()[0].strip()
            if latest.ai_key_points
            else ""
        ),
        "key_risks": (
            [l.strip() for l in latest.ai_risk_signals.splitlines() if l.strip()][:3]
            if latest.ai_risk_signals
            else []
        ),
    }


class ThesisReviewAgent:
    """AI agent for reviewing a single investment thesis."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def review(
        self,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions_with_ids: list[dict[str, object]],
        catalysts_with_ids: list[dict[str, object]],
        triggered_catalysts_with_ids: list[dict[str, object]] | None = None,
        current_price: float | None = None,
        entry_price: float | None = None,
        target_price: float | None = None,
        # Memory wiring params (optional, backward-compat)
        session: AsyncSession | None = None,
        user_id: str | None = None,
        thesis_id: int | None = None,
        trigger: str = "thesis_review",
    ) -> ThesisReviewOutput:
        """Run a thesis review and return structured output.

        Args:
            assumptions_with_ids:         Active assumptions — list[{"id": int, "description": str}].
            catalysts_with_ids:           PENDING catalysts — list[{"id": int, "description": str}].
            triggered_catalysts_with_ids: TRIGGERED catalysts — context only.
            session:                      Optional AsyncSession for memory logging.
            user_id:                      Optional user_id for episodic log.
            thesis_id:                    Optional thesis FK for traceability.
            trigger:                      Trigger label (default: thesis_review).

        Raises:
            AIError: If the API call fails after retries.
            ValueError: If the response cannot be parsed into ThesisReviewOutput.
        """
        # --- Memory: fetch episodes + rendered context (Layer 2 + 3) ---
        # Returns raw filtered episodes alongside the rendered string so we
        # can extract previous_review without a second DB call.
        filtered_episodes, memory_block = await _fetch_memory_for_review_full(
            session=session,
            user_id=user_id,
            ticker=ticker,
            thesis_id=thesis_id,
        )

        # --- P2: Extract previous verdict anchor from episodes ---
        # Gives the LLM explicit memory of what it decided last time,
        # enabling SYSTEM_PROMPT rules 7-10 (consistency enforcement) to
        # have concrete data to act on.
        previous_review = _extract_previous_review(filtered_episodes, thesis_id)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_review_prompt(
                    ticker=ticker,
                    thesis_title=thesis_title,
                    thesis_summary=thesis_summary,
                    assumptions_with_ids=assumptions_with_ids,
                    catalysts_with_ids=catalysts_with_ids,
                    triggered_catalysts_with_ids=triggered_catalysts_with_ids or [],
                    current_price=current_price,
                    entry_price=entry_price,
                    target_price=target_price,
                    memory_context=memory_block,
                    previous_review=previous_review,
                ),
            },
        ]

        logger.info(
            "thesis_review_agent.start",
            ticker=ticker,
            has_memory=bool(memory_block),
            has_previous_review=previous_review is not None,
            previous_verdict=previous_review.get("verdict") if previous_review else None,
        )

        try:
            response = await self._client.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=_MAX_TOKENS,
            )
            raw_text = self._client.extract_text(response)
            clean_text = _extract_json(raw_text)
            logger.debug(
                "thesis_review_agent.raw_response",
                ticker=ticker,
                raw_length=len(raw_text),
                clean_length=len(clean_text),
            )
            data = json.loads(clean_text)
            result = ThesisReviewOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "thesis_review_agent.parse_error",
                ticker=ticker,
                error=str(exc),
                raw_text=raw_text[:500] if "raw_text" in dir() else "unavailable",
            )
            raise ValueError(f"Failed to parse AI response for {ticker}: {exc}") from exc
        except AIError:
            logger.error("thesis_review_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "thesis_review_agent.complete",
            ticker=ticker,
            verdict=result.overall_verdict,
            confidence=result.confidence,
            verdict_changed=(
                previous_review is not None
                and previous_review.get("verdict") != str(result.overall_verdict)
            ),
        )

        # --- Memory: log interaction (Layer 2) ---
        await _log_thesis_review_interaction(
            session=session,
            user_id=user_id,
            ticker=ticker,
            result=result,
            thesis_id=thesis_id,
            trigger=trigger,
        )

        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch_memory_for_review_full(
    session,
    user_id: str | None,
    ticker: str,
    thesis_id: int | None = None,
) -> tuple[list, str]:
    """Fetch episodic + semantic memory and return both raw episodes and rendered string.

    Returns:
        (filtered_episodes, rendered_memory_block)
        filtered_episodes: list[AIInteractionLog] scoped to thesis + ticker.
        rendered_memory_block: str ready for prompt injection (empty if no data).

    The caller (ThesisReviewAgent.review) uses filtered_episodes to extract
    previous_review without triggering a second DB call.

    Scoping layers (defense-in-depth):
      1. thesis_id filter in get_memory_context() — primary scope
      2. ticker filter applied here — secondary scope for cross-thesis safety

    Never raises — memory failure must not block AI review calls.
    """
    if session is None or not user_id:
        return [], ""
    try:
        from src.ai.memory.memory_service import MemoryService

        mem_ctx = await MemoryService.get_memory_context(
            session,
            user_id=user_id,
            episode_limit=10,
            thesis_id=thesis_id,
        )
        if mem_ctx.is_empty():
            return [], ""

        # Secondary filter: ticker-level scope on top of thesis_id scope.
        # Catches edge cases where thesis_id was not recorded on old entries.
        filtered_episodes = [
            ep for ep in mem_ctx.recent_episodes
            if not ep.tickers or ticker in ep.tickers
        ]
        if not filtered_episodes and mem_ctx.latest_snapshot is None:
            return [], ""

        mem_ctx.recent_episodes = filtered_episodes
        rendered = mem_ctx.render()
        logger.debug(
            "thesis_review_agent.memory_fetched",
            ticker=ticker,
            thesis_id=thesis_id,
            episodes=len(filtered_episodes),
            has_snapshot=mem_ctx.latest_snapshot is not None,
        )
        return filtered_episodes, rendered
    except Exception as exc:
        logger.warning(
            "thesis_review_agent.memory_fetch_failed",
            ticker=ticker,
            error=str(exc),
        )
        return [], ""


async def _fetch_memory_for_review(
    session,
    user_id: str | None,
    ticker: str,
    thesis_id: int | None = None,
) -> str:
    """Thin wrapper — returns rendered memory string only.

    Kept for backward compatibility. Internally delegates to
    _fetch_memory_for_review_full and discards the episodes.
    Use _fetch_memory_for_review_full when episodes are needed.
    """
    _, rendered = await _fetch_memory_for_review_full(
        session=session,
        user_id=user_id,
        ticker=ticker,
        thesis_id=thesis_id,
    )
    return rendered


async def _log_thesis_review_interaction(
    session,
    user_id: str | None,
    ticker: str,
    result: ThesisReviewOutput,
    thesis_id: int | None,
    trigger: str,
) -> None:
    """Fire-and-forget memory log. Never raises."""
    if session is None or not user_id:
        return
    try:
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        # key_points: top 5 recommendation summaries
        key_lines: list[str] = []
        for rec in (getattr(result, "recommendations", []) or [])[:5]:
            if hasattr(rec, "action"):
                key_lines.append(str(rec.action))
            elif isinstance(rec, str):
                key_lines.append(rec)

        # risk_signals: invalidation risks or bearish signals
        risk_lines: list[str] = []
        for risk in (getattr(result, "invalidation_risks", []) or [])[:3]:
            risk_lines.append(str(risk))
        if not risk_lines:
            for risk in (getattr(result, "risk_signals", []) or [])[:3]:
                risk_lines.append(str(risk.signal) if hasattr(risk, "signal") else str(risk))

        entry = InteractionEntry(
            user_id=user_id,
            agent_type="thesis_review",
            trigger=trigger,
            tickers=[ticker],
            ai_verdict=str(result.overall_verdict or ""),
            ai_confidence=getattr(result, "confidence", None),
            ai_key_points="\n".join(key_lines) if key_lines else None,
            ai_risk_signals="\n".join(risk_lines) if risk_lines else None,
            thesis_id=thesis_id,
        )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning(
            "thesis_review_agent.memory_log_failed",
            ticker=ticker,
            error=str(exc),
        )
