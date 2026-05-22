"""NextActionSuggester — cross-agent synthesis into ordered investor action list.

Owner: ai segment.
Caller: BriefingService (after brief + judge batch), bot scheduler (morning/EOD),
        API /next-actions endpoint.

Distinct from BriefingAgent.ActionQueue:
  - ActionQueue:       macro-level priority labels, single-agent output.
  - NextActionSuggester: per-ticker specific steps, cross-references outputs
                         from ThesisJudge + InvalidationDetector + Watchdog
                         + SignalEngine → ordered NextActionPlan.

Boundary:
  - Reads only the data passed in — no DB calls, no market API calls.
  - Does NOT write to DB — caller owns persistence.
  - bot and api may call this through BriefingService or a dedicated
    next-action service; not directly from command handlers.

Fallback:
  - If AI is unavailable, returns a rule-based NextActionPlan derived from
    urgency signals already present in the input contexts.
    confidence=0.3 on all fallback actions.

Memory logging (Wave 6):
  - suggest() accepts optional session + user_id params.
  - On AI success, plan-level summary is logged as an episodic entry.
  - trigger=next_action_plan captures the cross-agent synthesis moment —
    the highest-level investor intent signal in the system.
  - Caller owns session — suggester never opens DB directly (boundary preserved).
  - Backward-compat: session=None skips logging silently.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from src.ai.client import AIClient, AIError
from src.ai.schemas.next_action import ActionScope, NextActionPlan, SuggestedAction
from src.platform.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là AI hỗ trợ nhà đầu tư chứng khoán Việt Nam quyết định hành động tiếp theo.
Nhiệm vụ: tổng hợp tất cả tín hiệu từ nhiều nguồn phân tích và tạo danh sách
hành động ưu tiên cụ thể, theo thứ tự khẩn cấp giảm dần.

Quy tắc:
- Chỉ trả về JSON hợp lệ theo schema được cung cấp.
- actions phải được sắp xếp theo urgency_score DESC (cao nhất trước).
- Mỗi action phải có step cụ thể — không chung chung như "đắp thêm tích lũy".
- title < 10 từ, viết ngắn gọn cho bot alert.
- rationale giải thích TẠI SAO hôm nay, không phải tổng quan thesis.
- source_signals liệt kê agent/signal đã trigger, e.g. "ThesisJudge:WEAKENING".
- summary: 1-2 câu tổng hợp toàn bộ plan cho đầu message.
- Không bịa đặt thông tin không có trong input.
- Tối đa 8 actions — ưu tiên chất lượng hơn số lượng.
"""


def _build_prompt(contexts: list[dict[str, Any]]) -> str:
    """Build user prompt from list of ticker/signal contexts."""
    lines = ["## Input Signal Contexts"]
    for i, ctx in enumerate(contexts, 1):
        ticker = ctx.get("ticker", "UNKNOWN")
        lines.append(f"\n### [{i}] {ticker}")
        if ctx.get("thesis_id"):
            lines.append(f"thesis_id: {ctx['thesis_id']}")
        if ctx.get("thesis_title"):
            lines.append(f"thesis: {ctx['thesis_title']}")
        if ctx.get("judge_verdict"):
            lines.append(f"ThesisJudge: verdict={ctx['judge_verdict']} "
                         f"delta={ctx.get('conviction_delta', 'N/A')} "
                         f"action={ctx.get('judge_action', 'N/A')}")
        if ctx.get("invalidation_verdict"):
            lines.append(f"Invalidation: verdict={ctx['invalidation_verdict']} "
                         f"breach={ctx.get('breach_type', 'N/A')} "
                         f"action={ctx.get('invalidation_action', 'N/A')}")
        if ctx.get("watchdog_verdict"):
            lines.append(f"Watchdog: verdict={ctx['watchdog_verdict']} "
                         f"urgency={ctx.get('watchdog_urgency', 'N/A')} "
                         f"health={ctx.get('health_score', 'N/A')}")
        if ctx.get("signal_urgency"):
            lines.append(f"SignalEngine: urgency={ctx['signal_urgency']} "
                         f"ranked_signals={ctx.get('top_signals', [])}")
        if ctx.get("stop_loss_breached"):
            lines.append("⚠️ stop_loss BREACHED")
        if ctx.get("notes"):
            lines.append(f"notes: {ctx['notes']}")

    lines += [
        "\n## Expected JSON output",
        "{",
        '  "summary": "<1-2 câu tổng hợp>",',
        '  "actions": [',
        '    {',
        '      "ticker": "<mã hoặc PORTFOLIO>",',
        '      "thesis_id": "<id hoặc null>",',
        '      "scope": "<ActionScope value>",',
        '      "urgency": "critical|high|medium|low",',
        '      "urgency_score": <float 0.0-1.0>,',
        '      "title": "<< 10 từ>",',
        '      "step": "<bước hành động cụ thể>",',
        '      "rationale": "<lý do 1-2 câu>",',
        '      "source_signals": ["..."],',
        '      "confidence": <float 0.0-1.0>',
        '    }',
        '  ]',
        '}',
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

_URGENCY_SCORE_MAP = {
    "critical": 0.95,
    "CRITICAL": 0.95,
    "high": 0.70,
    "HIGH": 0.70,
    "medium": 0.45,
    "MEDIUM": 0.45,
    "low": 0.20,
    "LOW": 0.20,
}

# Maps verdict → (urgency, ActionScope) for _fallback_plan rule engine.
# Priority order (highest severity first):
#   INVALIDATED / CONFIRMED_INVALID → critical, force exit consideration
#   WEAKENING / SUSPECTED           → high, thesis review
#   REVIEW_NOW                      → high, thesis review
#   BEARISH (watchdog proxy)        → high, signal respond
# ON_TRACK is intentionally absent — no fallback action needed.
_VERDICT_FALLBACK: dict[str, tuple[str, ActionScope]] = {
    "INVALIDATED":       ("critical", ActionScope.THESIS_INVALIDATE),
    "CONFIRMED_INVALID": ("critical", ActionScope.THESIS_INVALIDATE),
    "WEAKENING":         ("high",     ActionScope.THESIS_REVIEW),
    "SUSPECTED":         ("high",     ActionScope.THESIS_REVIEW),
    "REVIEW_NOW":        ("high",     ActionScope.THESIS_REVIEW),
    "BEARISH":           ("high",     ActionScope.SIGNAL_RESPOND),
}


def _fallback_plan(contexts: list[dict[str, Any]]) -> NextActionPlan:
    """Rule-based NextActionPlan when AI is unavailable.

    Derives urgency and scope from the highest-severity signal per ticker
    using _VERDICT_FALLBACK. All fallback actions have confidence=0.3.
    """
    actions: list[SuggestedAction] = []

    for ctx in contexts:
        ticker = ctx.get("ticker", "UNKNOWN")

        invalidation_verdict = ctx.get("invalidation_verdict", "")
        judge_verdict        = ctx.get("judge_verdict", "")
        watchdog_verdict     = (ctx.get("watchdog_verdict") or "").upper()
        watchdog_urgency     = (ctx.get("watchdog_urgency") or "").upper()
        stop_loss_breached   = ctx.get("stop_loss_breached", False)

        # --- resolve urgency + scope via priority cascade ---
        if stop_loss_breached or invalidation_verdict in ("CONFIRMED", "CONFIRMED_INVALID"):
            urgency = "critical"
            scope   = ActionScope.THESIS_INVALIDATE
            title   = f"{ticker}: Cân nhắc thoát vị thế"
            step    = "Kiểm tra lại stop-loss và xem xét đóng vị thế nếu thesis bị vô hiệu hóa."
            source  = [
                f"invalidation:{invalidation_verdict}",
                *(["stop_loss_breach"] if stop_loss_breached else []),
            ]
        elif judge_verdict and judge_verdict in _VERDICT_FALLBACK:
            urgency, scope = _VERDICT_FALLBACK[judge_verdict]
            title   = f"{ticker}: Review thesis ngay"
            step    = "Chạy ThesisReview hoặc review thủ công các assumptions đang bị thách thức."
            source  = [f"ThesisJudge:{judge_verdict}"]
            if invalidation_verdict:
                source.append(f"Invalidation:{invalidation_verdict}")
        elif invalidation_verdict in _VERDICT_FALLBACK:
            urgency, scope = _VERDICT_FALLBACK[invalidation_verdict]
            title   = f"{ticker}: Review thesis ngay"
            step    = "Chạy ThesisReview hoặc review thủ công các assumptions đang bị thách thức."
            source  = [f"Invalidation:{invalidation_verdict}"]
        elif watchdog_verdict == "BEARISH" or watchdog_urgency in ("HIGH", "CRITICAL"):
            urgency, scope = _VERDICT_FALLBACK["BEARISH"]
            title   = f"{ticker}: Tín hiệu tiêu cực"
            step    = "Theo dõi sát diễn biến giá và dòng tiền. Cân nhắc reduce nếu tín hiệu duy trì."
            source  = [f"Watchdog:{watchdog_verdict}:{watchdog_urgency}"]
        else:
            urgency = "low"
            scope   = ActionScope.WATCHLIST_MONITOR
            title   = f"{ticker}: Tiếp tục theo dõi"
            step    = "Không có tín hiệu bất thường. Duy trì monitoring theo kế hoạch."
            source  = ["no_breach_detected"]

        actions.append(
            SuggestedAction(
                ticker=ticker,
                thesis_id=ctx.get("thesis_id"),
                scope=scope,
                urgency=urgency,  # type: ignore[arg-type]
                urgency_score=_URGENCY_SCORE_MAP.get(urgency, 0.3),
                title=title,
                step=step,
                rationale="[Fallback] AI không khả dụng — rule-based từ signal contexts.",
                source_signals=[s for s in source if s],
                confidence=0.3,
            )
        )

    actions.sort(key=lambda a: a.urgency_score, reverse=True)
    critical_count = sum(1 for a in actions if a.urgency == "critical")

    return NextActionPlan(
        actions=actions,
        summary="[Fallback] Kế hoạch hành động được tạo tự động từ rule engine. Vui lòng review thủ công.",
        total_critical=critical_count,
        generated_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class NextActionSuggester:
    """Cross-agent synthesis: produces ordered investor action list.

    Aggregates outputs from ThesisJudgeAgent, ThesisInvalidationDetector,
    WatchdogAgent, and SignalEngineAgent into a single NextActionPlan.

    Call pattern::

        suggester = NextActionSuggester(ai_client)

        contexts = [
            {
                "ticker": "VHM",
                "thesis_id": "42",
                "thesis_title": "VHM phục hồi sau chu kỳ margin call",
                "judge_verdict": "WEAKENING",
                "conviction_delta": -0.35,
                "judge_action": "review",
                "watchdog_verdict": "BEARISH",
                "watchdog_urgency": "HIGH",
                "health_score": 42,
                "invalidation_verdict": "SUSPECTED",
                "breach_type": "ASSUMPTION_RATIO",
                "invalidation_action": "review",
                "stop_loss_breached": False,
                "signal_urgency": "HIGH",
                "top_signals": ["foreign_sell", "volume_drop"],
                "notes": "KQKD Q1 dưới kỳ vọng 15%",
            },
        ]

        plan = await suggester.suggest(
            contexts,
            session=session,   # Wave 6: pass caller's session
            user_id=user_id,   # Wave 6: pass user_id for memory log
        )
        # plan.actions[0] = highest urgency action
        # plan.summary    = 1-2 câu tổng hợp
        # plan.total_critical = badge count
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def suggest(
        self,
        contexts: list[dict[str, Any]],
        session: Any = None,
        user_id: str | None = None,
    ) -> NextActionPlan:
        """Generate ordered NextActionPlan from cross-agent signal contexts.

        Returns NextActionPlan. Never raises — falls back to rule-based plan
        with confidence=0.3 on any AI or parse error.

        Args:
            contexts: List of per-ticker/portfolio signal dicts.
                      Expected keys (all optional, use what's available):
                        ticker, thesis_id, thesis_title,
                        judge_verdict, conviction_delta, judge_action,
                        watchdog_verdict, watchdog_urgency, health_score,
                        invalidation_verdict, breach_type, invalidation_action,
                        stop_loss_breached,
                        signal_urgency, top_signals,
                        notes.
            session:  Optional DB session from caller.
                      When provided, plan-level summary is logged as episodic memory.
            user_id:  Optional user ID for episodic memory logging.
        """
        if not contexts:
            return NextActionPlan(
                actions=[],
                summary="Không có context nào được cung cấp.",
                total_critical=0,
                generated_at=datetime.now(UTC).isoformat(),
            )

        user_prompt = _build_prompt(contexts)

        try:
            raw = await self._client.complete(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                temperature=0.3,
            )
            data = json.loads(raw)

            raw_actions = data.get("actions", [])
            actions = [SuggestedAction(**a) for a in raw_actions]
            actions.sort(key=lambda a: a.urgency_score, reverse=True)

            critical_count = sum(1 for a in actions if a.urgency == "critical")

            plan = NextActionPlan(
                actions=actions,
                summary=data.get("summary", ""),
                total_critical=critical_count,
                generated_at=datetime.now(UTC).isoformat(),
            )

            logger.info(
                "NextActionSuggester: %d actions generated, %d critical, tickers=%s",
                len(actions),
                critical_count,
                [a.ticker for a in actions[:3]],
            )
            await _log_next_action_interaction(session, user_id, plan)
            return plan

        except AIError as exc:
            is_rate = "rate" in type(exc).__name__.lower()
            if is_rate:
                logger.info(
                    "NextActionSuggester: rate limit, using fallback for %d contexts",
                    len(contexts),
                )
            else:
                logger.warning("NextActionSuggester: AI error: %s", exc)
            return _fallback_plan(contexts)

        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "NextActionSuggester: parse error — possible prompt regression: %s", exc
            )
            return _fallback_plan(contexts)

        except Exception as exc:
            logger.warning("NextActionSuggester: unexpected error: %s", exc)
            return _fallback_plan(contexts)


# ---------------------------------------------------------------------------
# Memory interaction logger — module-level helper (Wave 6)
# ---------------------------------------------------------------------------


async def _log_next_action_interaction(
    session: Any,
    user_id: str | None,
    plan: NextActionPlan,
) -> None:
    """Fire-and-forget memory log for next-action plan generation events.

    Caller owns the session — next_action_suggester never opens DB directly
    (boundary: ai segment, no direct DB access).

    next_action_plan events represent the highest-level investor intent signal
    in the system — the moment all cross-agent outputs are synthesized into
    a concrete ordered action list. Logging these lets the memory layer track:
      - What actions were prioritised across time (pattern of urgency distribution)
      - How often critical actions appear (investor risk exposure trend)
      - Which tickers recur in top-urgency slots (persistent watch signal)

    trigger=next_action_plan is deliberately coarse-grained (one event per
    synthesis run) to avoid duplicating the per-ticker signals already logged
    by ThesisJudge and InvalidationDetector.

    Only logs on AI-success path. Fallback plans are not logged — they carry
    no new information about investor behaviour, only system availability.

    Never raises. Silently skips when session is None or user_id unset.
    """
    if session is None or not user_id:
        return
    try:
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        tickers = list({
            a.ticker for a in (plan.actions or []) if a.ticker and a.ticker != "PORTFOLIO"
        })[:5]

        entry = InteractionEntry(
            user_id=user_id,
            agent_type="next_action_suggester",
            trigger="next_action_plan",
            tickers=tickers,
            ai_verdict=(plan.summary or "")[:120],
            ai_key_points=(
                f"total_actions={len(plan.actions)} "
                f"total_critical={plan.total_critical}"
            ),
        )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning("next_action_suggester.memory_log_failed", error=str(exc))
