"""ThesisInvalidationDetector — AI confirmation layer over rule-based breach detection.

Owner: ai segment.
Caller: thesis.invalidation_service — after InvalidationService.check_with_price()
        returns should_invalidate=True, caller passes the result here for:
          1. AI verdict confirmation (CONFIRMED / SUSPECTED / CLEARED).
          2. Investor-facing narrative for bot alert.
          3. Recommended action (exit_signal / review / reduce / hold).

Distinct from ThesisJudgeAgent:
  - ThesisJudgeAgent:           AI reasoning → conviction_delta → feeds briefing.
                                Triggered by SignalEngineOutput.thesis_review_triggers.
  - ThesisInvalidationDetector: Rule breach confirmed? → AI narrative + action.
                                Triggered by InvalidationService.check_with_price().
                                Does NOT produce conviction_delta.
                                SUSPECTED verdict → optionally hand off to ThesisJudgeAgent.

Boundary:
  - Reads only the data passed in — no DB calls, no market API calls.
  - Does NOT write to DB — caller (invalidation_service / ThesisService) owns that.
  - bot and api NEVER call this directly — only through thesis domain services.

Fallback:
  - If AI is unavailable, returns a rule-based InvalidationSignal with
    confidence=0.3 and verdict=CONFIRMED when stop_loss_breached or
    assumption_ratio breach detected. SUSPECTED in all other cases.

Memory logging (Wave 6):
  - detect() accepts optional session + user_id params.
  - Every verdict (AI + all fallback paths) is logged as an episodic entry.
  - Invalidation events are inflection points — the most semantically dense
    single data point in the system for detecting investor error patterns.
  - Caller owns session — detector never opens DB directly (boundary preserved).
  - Backward-compat: session=None skips logging silently.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from src.ai.client import AIClient, AIError
from src.ai.schemas.invalidation import BreachType, InvalidationSignal, InvalidationVerdict
from src.platform.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là một AI chuyên phân tích thesis đầu tư chứng khoán Việt Nam.
Nhiệm vụ: xác nhận hoặc bác bỏ tín hiệu vi phạm thesis dựa trên ngữ cảnh thị trường.

Quy tắc:
- Chỉ trả về JSON hợp lệ theo schema được cung cấp.
- verdict phải là một trong: CONFIRMED, SUSPECTED, CLEARED.
- action phải là một trong: exit_signal, review, reduce, hold.
- narrative viết cho nhà đầu tư (2-3 câu), không phải log kỹ thuật.
- breach_summary là 1 câu ngắn gọn cho bot alert.
- Nếu có yếu tố giảm nhẹ thực sự, liệt kê trong mitigating_factors.
- Không bịa đặt thông tin không có trong input.
"""


def _build_prompt(
    *,
    thesis_id: str | int,
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    breach_reason: str,
    breach_type: BreachType,
    stop_loss_breached: bool,
    current_price: float | None,
    stop_loss: float | None,
    invalid_assumptions: list[str],
    total_assumptions: int,
    watchdog_verdict: str | None,
    watchdog_urgency: str | None,
    score: float,
) -> str:
    lines = [
        f"## Thesis: {thesis_title} [{ticker}] (ID: {thesis_id})",
        f"Summary: {thesis_summary or 'Không có tóm tắt.'}",
        "",
        "## Breach Context",
        f"breach_type: {breach_type.value}",
        f"breach_reason: {breach_reason}",
        f"stop_loss_breached: {stop_loss_breached}",
    ]
    if current_price is not None and stop_loss is not None:
        lines.append(f"current_price: {current_price:,.0f} | stop_loss: {stop_loss:,.0f}")
    if invalid_assumptions:
        lines.append(f"invalid_assumptions ({len(invalid_assumptions)}/{total_assumptions}):")
        for a in invalid_assumptions:
            lines.append(f"  - {a}")
    lines += [
        "",
        "## Signal Context",
        f"thesis_score: {score:.1f}",
        f"watchdog_verdict: {watchdog_verdict or 'N/A'}",
        f"watchdog_urgency: {watchdog_urgency or 'N/A'}",
        "",
        "## Expected JSON output",
        "{",
        '  "verdict": "CONFIRMED" | "SUSPECTED" | "CLEARED",',
        '  "breach_summary": "<1 sentence for bot alert>",',
        '  "narrative": "<2-3 sentences for investor>",',
        '  "action": "exit_signal" | "review" | "reduce" | "hold",',
        '  "confidence": <float 0.0-1.0>,',
        '  "mitigating_factors": ["..."]',
        "}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def _fallback_verdict(
    stop_loss_breached: bool,
    assumption_ratio_breach: bool,
    watchdog_verdict: str | None,
    watchdog_urgency: str | None,
) -> tuple[InvalidationVerdict, Literal["exit_signal", "review", "reduce", "hold"]]:
    """Rule-based verdict when AI is unavailable."""
    v_upper = (watchdog_verdict or "").upper()
    u_upper = (watchdog_urgency or "").upper()
    is_bearish_critical = v_upper == "BEARISH" and u_upper == "CRITICAL"

    if stop_loss_breached or (assumption_ratio_breach and is_bearish_critical):
        return InvalidationVerdict.CONFIRMED, "exit_signal"
    if assumption_ratio_breach or is_bearish_critical:
        return InvalidationVerdict.SUSPECTED, "review"
    return InvalidationVerdict.SUSPECTED, "review"


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ThesisInvalidationDetector:
    """AI confirmation layer over InvalidationService rule-based breach detection.

    Call pattern::

        from thesis.invalidation_service import InvalidationService
        from src.ai.agents.invalidation_detector import ThesisInvalidationDetector

        rule_result = invalidation_svc.check_with_price(
            thesis, current_score=score, current_price=price
        )
        if rule_result.should_invalidate:
            detector = ThesisInvalidationDetector(ai_client)
            signal = await detector.detect(
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                thesis_title=thesis.title,
                thesis_summary=thesis.summary or "",
                breach_reason=rule_result.reason,
                stop_loss_breached=rule_result.stop_loss_breached,
                current_price=price,
                stop_loss=thesis.stop_loss,
                invalid_assumptions=rule_result.invalid_assumptions,
                total_assumptions=len(thesis.assumptions),
                score=rule_result.score,
                session=session,      # Wave 6: pass caller's session
                user_id=user_id,      # Wave 6: pass user_id for memory log
            )
            # signal.verdict → CONFIRMED / SUSPECTED / CLEARED
            # signal.action  → exit_signal / review / reduce / hold
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def detect(
        self,
        *,
        thesis_id: str | int,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        breach_reason: str,
        stop_loss_breached: bool = False,
        current_price: float | None = None,
        stop_loss: float | None = None,
        invalid_assumptions: list[str] | None = None,
        total_assumptions: int = 0,
        score: float = 0.0,
        watchdog_verdict: str | None = None,
        watchdog_urgency: str | None = None,
        session: Any = None,
        user_id: str | None = None,
    ) -> InvalidationSignal:
        """Run AI confirmation on a rule-based invalidation breach.

        Returns InvalidationSignal. Never raises — falls back to rule-based
        output with confidence=0.3 on any AI or parse error.

        Args:
            thesis_id:           Thesis ID for traceability.
            ticker:              Mã cổ phiếu.
            thesis_title:        Tiêu đề thesis.
            thesis_summary:      Tóm tắt luận điểm (optional but improves AI quality).
            breach_reason:       Raw reason string from InvalidationCheckResult.reason.
            stop_loss_breached:  True if current_price ≤ stop_loss.
            current_price:       Giá hiện tại (VND).
            stop_loss:           Mức stop-loss của thesis (VND).
            invalid_assumptions: List of invalid assumption descriptions.
            total_assumptions:   Total number of assumptions in thesis.
            score:               Current thesis score (0-100).
            watchdog_verdict:    WatchdogOutput verdict string (optional).
            watchdog_urgency:    WatchdogOutput urgency string (optional).
            session:             Optional DB session from caller.
                                 When provided, result is logged as episodic memory.
            user_id:             Optional user ID for episodic memory logging.
        """
        invalid_assumptions = invalid_assumptions or []

        # Determine breach_type
        assumption_ratio_breach = (
            total_assumptions > 0
            and len(invalid_assumptions) / total_assumptions > 0.5
        )
        v_upper = (watchdog_verdict or "").upper()
        u_upper = (watchdog_urgency or "").upper()
        is_watchdog_critical = v_upper == "BEARISH" and u_upper == "CRITICAL"

        if stop_loss_breached and assumption_ratio_breach:
            breach_type = BreachType.COMPOSITE
        elif stop_loss_breached:
            breach_type = BreachType.STOP_LOSS
        elif assumption_ratio_breach:
            breach_type = BreachType.ASSUMPTION_RATIO
        elif is_watchdog_critical:
            breach_type = BreachType.WATCHDOG_CRITICAL
        else:
            breach_type = BreachType.COMPOSITE

        user_prompt = _build_prompt(
            thesis_id=thesis_id,
            ticker=ticker,
            thesis_title=thesis_title,
            thesis_summary=thesis_summary,
            breach_reason=breach_reason,
            breach_type=breach_type,
            stop_loss_breached=stop_loss_breached,
            current_price=current_price,
            stop_loss=stop_loss,
            invalid_assumptions=invalid_assumptions,
            total_assumptions=total_assumptions,
            watchdog_verdict=watchdog_verdict,
            watchdog_urgency=watchdog_urgency,
            score=score,
        )

        try:
            api_resp = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.2,
            )
            raw = self._client.extract_text(api_resp)
            data = json.loads(raw)
            signal = InvalidationSignal(
                thesis_id=str(thesis_id),
                ticker=ticker,
                breach_type=breach_type,
                **data,
            )
            signal.checked_at = datetime.now(UTC).isoformat()

            logger.info(
                "InvalidationDetector: thesis=%s ticker=%s verdict=%s action=%s confidence=%.2f",
                thesis_id, ticker, signal.verdict, signal.action, signal.confidence,
            )
            await _log_invalidation_interaction(session, user_id, signal)
            return signal

        except AIError as exc:
            is_rate = "rate" in type(exc).__name__.lower()
            if is_rate:
                logger.info(
                    "InvalidationDetector: rate limit thesis=%s ticker=%s, using fallback",
                    thesis_id, ticker,
                )
            else:
                logger.warning(
                    "InvalidationDetector: AI error thesis=%s ticker=%s: %s",
                    thesis_id, ticker, exc,
                )
            fallback = self._fallback(
                thesis_id=thesis_id,
                ticker=ticker,
                breach_type=breach_type,
                breach_reason=breach_reason,
                stop_loss_breached=stop_loss_breached,
                assumption_ratio_breach=assumption_ratio_breach,
                watchdog_verdict=watchdog_verdict,
                watchdog_urgency=watchdog_urgency,
            )
            await _log_invalidation_interaction(session, user_id, fallback)
            return fallback

        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "InvalidationDetector: parse error thesis=%s ticker=%s — possible prompt regression: %s",
                thesis_id, ticker, exc,
            )
            fallback = self._fallback(
                thesis_id=thesis_id,
                ticker=ticker,
                breach_type=breach_type,
                breach_reason=breach_reason,
                stop_loss_breached=stop_loss_breached,
                assumption_ratio_breach=assumption_ratio_breach,
                watchdog_verdict=watchdog_verdict,
                watchdog_urgency=watchdog_urgency,
            )
            await _log_invalidation_interaction(session, user_id, fallback)
            return fallback

        except Exception as exc:
            logger.warning(
                "InvalidationDetector: unexpected error thesis=%s ticker=%s: %s",
                thesis_id, ticker, exc,
            )
            fallback = self._fallback(
                thesis_id=thesis_id,
                ticker=ticker,
                breach_type=breach_type,
                breach_reason=breach_reason,
                stop_loss_breached=stop_loss_breached,
                assumption_ratio_breach=assumption_ratio_breach,
                watchdog_verdict=watchdog_verdict,
                watchdog_urgency=watchdog_urgency,
            )
            await _log_invalidation_interaction(session, user_id, fallback)
            return fallback

    def _fallback(
        self,
        *,
        thesis_id: str | int,
        ticker: str,
        breach_type: BreachType,
        breach_reason: str,
        stop_loss_breached: bool,
        assumption_ratio_breach: bool,
        watchdog_verdict: str | None,
        watchdog_urgency: str | None,
    ) -> InvalidationSignal:
        """Rule-based fallback when AI is unavailable. confidence=0.3."""
        verdict, action = _fallback_verdict(
            stop_loss_breached=stop_loss_breached,
            assumption_ratio_breach=assumption_ratio_breach,
            watchdog_verdict=watchdog_verdict,
            watchdog_urgency=watchdog_urgency,
        )
        return InvalidationSignal(
            thesis_id=str(thesis_id),
            ticker=ticker,
            verdict=verdict,
            breach_type=breach_type,
            breach_summary=f"[Fallback] {breach_reason}",
            narrative=(
                f"Rule-based fallback — AI không khả dụng. "
                f"Phát hiện: {breach_reason}. "
                f"Vui lòng review thesis thủ công."
            ),
            action=action,
            confidence=0.3,
            mitigating_factors=[],
            checked_at=datetime.now(UTC).isoformat(),
        )


# ---------------------------------------------------------------------------
# Memory interaction logger — module-level helper (Wave 6)
# ---------------------------------------------------------------------------


async def _log_invalidation_interaction(
    session: Any,
    user_id: str | None,
    result: InvalidationSignal,
) -> None:
    """Fire-and-forget memory log for invalidation detection events.

    Caller owns the session — invalidation_detector never opens DB directly
    (boundary: ai segment, no direct DB access).

    Invalidation events are inflection points — the moment a thesis crosses
    from 'valid' to 'breached'. The verdict (CONFIRMED / SUSPECTED / CLEARED)
    and breach_type together are the most semantically dense single data point
    in the system for detecting investor error patterns over time.

    trigger=breach:<breach_type> enables precise semantic grouping downstream:
      - breach:STOP_LOSS     → price discipline pattern
      - breach:ASSUMPTION_RATIO → thesis quality pattern
      - breach:COMPOSITE     → compound risk pattern
      - breach:WATCHDOG_CRITICAL → signal sensitivity pattern

    Logs every verdict including fallback (confidence=0.3) so the memory layer
    tracks AI-availability trends as a meta-signal.

    Never raises. Silently skips when session is None or user_id unset.
    """
    if session is None or not user_id:
        return
    try:
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        ticker = getattr(result, "ticker", "") or ""
        verdict = str(getattr(result, "verdict", "") or "")
        action = str(getattr(result, "action", "") or "")
        confidence = getattr(result, "confidence", 0.0) or 0.0
        breach_type = getattr(result, "breach_type", None)
        breach_str = (
            breach_type.value
            if hasattr(breach_type, "value")
            else str(breach_type or "")
        )
        breach_summary = str(getattr(result, "breach_summary", "") or "")

        entry = InteractionEntry(
            user_id=user_id,
            agent_type="invalidation_detector",
            trigger=f"breach:{breach_str}",
            tickers=[ticker] if ticker else [],
            ai_verdict=verdict,
            ai_key_points=(
                f"action={action} "
                f"confidence={confidence:.2f} "
                f"breach={breach_str} "
                f"summary={breach_summary[:80]}"
            ),
        )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning("invalidation_detector.memory_log_failed", error=str(exc))
