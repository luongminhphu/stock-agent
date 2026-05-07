"""StressTestAgent — simulate adverse scenarios against an investment thesis.

Owner: ai segment.
Callers:
    - thesis.StressTestService          → stress_test()  [primary path]
    - thesis.review_service (optional)  → run()          [legacy enrichment]
    - bot/commands/stress_test          → via StressTestService only

Boundary:
    - Accepts raw thesis data + scenario descriptions (no domain model imports).
    - Returns src.ai.schemas.StressTestOutput (canonical) from stress_test().
    - Returns local StressTestOutput from run() for backward compat.
    - Does NOT write DB, does NOT call thesis repositories.

Design note:
    sonar-pro does NOT support response_format={"type": "json_object"}.
    All AI calls use client.chat() which enforces JSON via system prompt.
    Never pass response_format to chat_completion() in this file.

max_tokens note:
    stress_test() passes AIClient.COMPLEX_MAX_TOKENS (8192) explicitly.
    A thesis with 5+ assumptions + 5 triggers + 3 macro_risks + reasoning
    easily exceeds 4000 chars — default 4096 tokens was causing truncation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.ai.client import AIClient, AIError
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Legacy local schema — kept for run() backward compat only
# New callers must use src.ai.schemas.StressTestOutput
# ---------------------------------------------------------------------------

class ScenarioResult(BaseModel):
    """[DEPRECATED] Use src.ai.schemas.StressTestOutput instead."""
    scenario_name: str
    probability: str = Field(..., description="HIGH | MEDIUM | LOW")
    impact_on_thesis: str = Field(
        ..., description="INVALIDATES | WEAKENS | NEUTRAL | STRENGTHENS"
    )
    price_impact_estimate: str
    key_assumption_broken: str | None = None
    mitigation: str


class StressTestOutput(BaseModel):
    """[DEPRECATED] Use src.ai.schemas.StressTestOutput instead."""
    ticker: str
    overall_resilience: str = Field(..., description="STRONG | MODERATE | WEAK | FRAGILE")
    scenario_results: list[ScenarioResult]
    most_critical_risk: str
    recommended_hedge: str | None = None
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_LEGACY = """
Bạn là chuyên gia phân tích rủi ro đầu tư chứng khoán Việt Nam.

Nhiệm vụ: Stress-test một investment thesis bằng cách mô phỏng các kịch bản bất lợi.

Quy trình:
1. Với mỗi kịch bản: đánh giá xác suất, mức độ ảnh hưởng lên thesis, ước tính tác động giá
2. Xác định assumption nào bị phá vỡ nếu kịch bản xảy ra
3. Đề xuất biện pháp hedge
4. Đánh giá overall resilience của thesis

Output JSON theo schema. Không có markdown, không có prose thêm.
"""

_SYSTEM_PROMPT_CANONICAL = """
Bạn là chuyên gia phân tích rủi ro đầu tư chứng khoán Việt Nam.
Nhiệm vụ: Đóng vai "phòng thủ đứng trước board" — tìm mọi lý do thesis có thể sại.

Quy trình:
1. Đọc thesis_title, thesis_summary, assumptions, catalysts, giá hiện tại.
2. Mô phỏng 1 stress_scenario bất lợi cụ thể (1 câu, có số).
3. Với từng assumption: phân loại INTACT | WEAKENED | BROKEN và giải thích ngắn.
4. Liệt kê đến 5 trigger cụ thể nhà đầu tư phải theo dõi.
5. Liệt kê 2-3 rủi ro vĩ mô Việt Nam phù hợp.
6. Đưa ra verdict BULLISH | NEUTRAL | BEARISH, xác suất invalidation 0.0-1.0, confidence 0.0-1.0.
7. Tóm tắt lý lẽ trong 2-3 câu (tiếng Việt).

Output JSON theo đúng schema sau — không markdown, không prose:
{
  "ticker": str,
  "thesis_title": str,
  "stress_scenario": str,
  "verdict": "BULLISH" | "NEUTRAL" | "BEARISH",
  "invalidation_probability": float,
  "confidence": float,
  "threatened_assumptions": [
    {"assumption_id": int | null, "description": str, "threat_level": "INTACT" | "WEAKENED" | "BROKEN", "counter_argument": str}
  ],
  "surviving_assumptions": [str],
  "suggested_triggers_to_watch": [str],
  "macro_risks": [str],
  "reasoning": str
}
"""


class StressTestAgent:
    """Simulates adverse scenarios against a thesis.

    Owner: ai segment.

    Two public methods:
        stress_test() — canonical path used by StressTestService.
                         Returns src.ai.schemas.StressTestOutput.
        run()          — legacy path for backward compat.
                         Returns local StressTestOutput.

    IMPORTANT: Both methods use client.chat() — never chat_completion() with
    response_format. sonar-pro rejects {"type": "json_object"} with HTTP 400.
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Canonical method — used by StressTestService
    # ------------------------------------------------------------------

    async def stress_test(
        self,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions: list[dict],
        catalysts: list[str],
        current_price: float | None = None,
        entry_price: float | None = None,
        target_price: float | None = None,
        stop_loss: float | None = None,
        macro_context: str = "",
        session: "AsyncSession | None" = None,
        user_id: str | None = None,
    ) -> object:
        """Run adversarial stress-test and return canonical StressTestOutput.

        Uses client.chat() with COMPLEX_MAX_TOKENS (8192) to prevent JSON
        truncation on theses with many assumptions (5+), triggers, and
        macro_risks fields.
        """
        from src.ai.schemas import StressTestOutput as CanonicalOutput

        investor_profile = await self._build_investor_profile(session, user_id)

        assumptions_text = "\n".join(
            f"- [{a.get('id', '?')}] {a.get('description', '')} (status: {a.get('status', 'valid')})"
            for a in assumptions
        ) or "(chưa có assumptions)"

        catalysts_text = "\n".join(f"- {c}" for c in catalysts) or "(không có catalyst pending)"

        price_block = ""
        if current_price is not None:
            price_block = f"Giá hiện tại: {current_price:,.0f} VNĐ"
            if entry_price:
                pnl = (current_price - entry_price) / entry_price * 100
                price_block += f" | Entry: {entry_price:,.0f} | P&L: {pnl:+.1f}%"
            if target_price:
                upside = (target_price - current_price) / current_price * 100
                price_block += f" | Target: {target_price:,.0f} (+{upside:.1f}%)"
            if stop_loss:
                downside = (stop_loss - current_price) / current_price * 100
                price_block += f" | Stop: {stop_loss:,.0f} ({downside:.1f}%)"

        user_prompt = (
            f"Ticker: {ticker}\n"
            f"Thesis: {thesis_title}\n\n"
            f"## Summary\n{thesis_summary}\n\n"
            f"## Assumptions\n{assumptions_text}\n\n"
            f"## Catalysts (pending)\n{catalysts_text}\n"
        )
        if price_block:
            user_prompt += f"\n## Price Context\n{price_block}\n"
        if macro_context:
            user_prompt += f"\n## Market Context\n{macro_context}\n"
        if investor_profile:
            user_prompt += f"\n{investor_profile}"

        logger.info("stress_test_agent.stress_test.start", ticker=ticker, thesis_title=thesis_title)

        try:
            result = await self._client.chat(
                system_prompt=_SYSTEM_PROMPT_CANONICAL,
                user_prompt=user_prompt,
                response_schema=CanonicalOutput,
                temperature=0.2,
                max_tokens=AIClient.COMPLEX_MAX_TOKENS,  # 8192 — prevents truncation on complex theses
            )
        except AIError:
            logger.error("stress_test_agent.stress_test.api_error", ticker=ticker)
            raise

        logger.info(
            "stress_test_agent.stress_test.complete",
            ticker=ticker,
            verdict=str(result.verdict),
            invalidation_prob=result.invalidation_probability,
        )

        await self._log_interaction_canonical(
            session=session,
            user_id=user_id,
            ticker=ticker,
            result=result,
        )

        return result

    # ------------------------------------------------------------------
    # Legacy method — backward compat for review_service etc.
    # ------------------------------------------------------------------

    async def run(
        self,
        ticker: str,
        thesis_summary: str,
        assumptions: list[str],
        scenarios: list[str] | None = None,
        session: "AsyncSession | None" = None,
        user_id: str | None = None,
        trigger: str = "stress_test",
    ) -> StressTestOutput:
        """[LEGACY] Run stress test scenarios. Returns local StressTestOutput.

        Uses client.chat() — JSON enforced via system prompt, not response_format.
        Prefer stress_test() for new callers.
        """
        investor_profile = await self._build_investor_profile(session, user_id)
        scenario_block = (
            "\n".join(f"- {s}" for s in scenarios)
            if scenarios
            else "(Tự generate 4-5 kịch bản bất lợi phù hợp nhất)"
        )
        user_prompt = (
            f"Ticker: {ticker}\n\n"
            f"## Thesis\n{thesis_summary}\n\n"
            f"## Assumptions\n" + "\n".join(f"- {a}" for a in assumptions) + "\n\n"
            f"## Scenarios to test\n{scenario_block}"
        )
        if investor_profile:
            user_prompt += f"\n\n{investor_profile}"

        logger.info("stress_test_agent.run.start", ticker=ticker)

        try:
            result = await self._client.chat(
                system_prompt=_SYSTEM_PROMPT_LEGACY,
                user_prompt=user_prompt,
                response_schema=StressTestOutput,
                temperature=0.2,
            )
        except AIError:
            logger.error("stress_test_agent.run.api_error", ticker=ticker)
            raise

        logger.info(
            "stress_test_agent.run.complete",
            ticker=ticker,
            resilience=result.overall_resilience,
        )

        await self._log_interaction(
            session=session,
            user_id=user_id,
            ticker=ticker,
            result=result,
            trigger=trigger,
        )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_investor_profile(self, session, user_id: str | None) -> str:
        if session is None:
            return ""
        try:
            from src.ai.context_builder import ContextBuilder, render_for_agent
            ctx = await ContextBuilder(session).build(user_id=user_id)
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning("stress_test_agent.investor_profile_failed", error=str(exc))
            return ""

    async def _log_interaction_canonical(self, session, user_id: str | None, ticker: str, result) -> None:
        if session is None or not user_id:
            return
        try:
            from src.ai.memory.memory_service import InteractionEntry, MemoryService
            threatened = getattr(result, "threatened_assumptions", []) or []
            critical = [
                f"{a.description[:60]}: {a.threat_level}"
                for a in threatened
                if str(getattr(a, "threat_level", "")).upper() in ("BROKEN", "WEAKENED")
            ][:5]
            entry = InteractionEntry(
                user_id=user_id,
                agent_type="stress_test",
                trigger="stress_test_canonical",
                tickers=[ticker],
                ai_verdict=str(getattr(result, "verdict", "")),
                ai_confidence=float(getattr(result, "confidence", 0.0)),
                ai_key_points=getattr(result, "reasoning", "")[:300] if getattr(result, "reasoning", "") else None,
                ai_risk_signals="\n".join(critical) if critical else None,
            )
            await MemoryService.log_interaction(session, entry)
        except Exception as exc:
            logger.warning("stress_test_agent.memory_log_canonical_failed", ticker=ticker, error=str(exc))

    async def _log_interaction(self, session, user_id: str | None, ticker: str, result: StressTestOutput, trigger: str) -> None:
        if session is None or not user_id:
            return
        try:
            from src.ai.memory.memory_service import InteractionEntry, MemoryService
            critical_scenarios = [
                f"{s.scenario_name}: {s.impact_on_thesis}"
                for s in (result.scenario_results or [])
                if s.impact_on_thesis in ("INVALIDATES", "WEAKENS")
            ][:5]
            entry = InteractionEntry(
                user_id=user_id,
                agent_type="stress_test",
                trigger=trigger,
                tickers=[ticker],
                ai_verdict=result.overall_resilience,
                ai_confidence=None,
                ai_key_points=result.most_critical_risk[:300] if result.most_critical_risk else None,
                ai_risk_signals="\n".join(critical_scenarios) if critical_scenarios else None,
            )
            await MemoryService.log_interaction(session, entry)
        except Exception as exc:
            logger.warning("stress_test_agent.memory_log_failed", ticker=ticker, error=str(exc))
