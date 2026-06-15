"""Watchdog Agent — evaluates thesis health for Invalidation Trigger Watchdog.

Owner: ai segment.
Consumed by: thesis.WatchdogService.

Responsibility boundary:
  - Accepts WatchdogContext, calls AI, returns ThesisHealthScore.
  - Does NOT write to DB, does NOT trigger alerts, does NOT modify thesis state.
  - Caller (WatchdogService) owns alert routing and DB writes.

Graceful degrade: returns None on any failure so the watchdog pipeline
never blocks other thesis operations.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.ai.client import AIClient
from src.ai.prompts.watchdog import WatchdogContext, build_user_prompt, SYSTEM_PROMPT
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class ThreatDetail(BaseModel):
    """Threat assessment for a single assumption."""

    assumption_id: int
    description: str
    threat_level: str = Field(..., description="none | low | medium | high")
    threat_reason: str

    @property
    def is_threatened(self) -> bool:
        return self.threat_level in ("medium", "high")


class ThesisHealthScore(BaseModel):
    """Full health assessment for one thesis."""

    health_score: int = Field(..., ge=0, le=100)
    overall_health: str = Field(..., description="HEALTHY | WARNING | CRITICAL")
    threatened_assumptions: list[ThreatDetail] = Field(default_factory=list)
    recommended_action: str = Field(
        ..., description="HOLD | REVIEW_SOON | REVIEW_URGENT | CONSIDER_EXIT"
    )
    summary: str = ""
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")

    @property
    def alert_level(self) -> str:
        """Map overall_health to 3-tier alert level for WatchdogService."""
        return {
            "HEALTHY": "OK",
            "WARNING": "SILENT_WARNING",
            "CRITICAL": "URGENT_ALERT",
        }.get(self.overall_health, "OK")

    @property
    def high_threat_count(self) -> int:
        return sum(1 for t in self.threatened_assumptions if t.threat_level == "high")

    @property
    def medium_threat_count(self) -> int:
        return sum(1 for t in self.threatened_assumptions if t.threat_level == "medium")

    def discord_summary(self, ticker: str) -> str:
        """Short embed text for Discord alert."""
        icon = {"HEALTHY": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}.get(self.overall_health, "⚪")
        threatened = [
            t.description[:60] for t in self.threatened_assumptions if t.is_threatened
        ]
        lines = [
            f"{icon} **{ticker}** — {self.overall_health} ({self.health_score}/100)",
            f"📊 {self.recommended_action} | Confidence: {self.confidence}",
        ]
        if threatened:
            lines.append("🚫 Assumptions bị đe dọa: " + "; ".join(threatened))
        if self.summary:
            lines.append(f"📝 {self.summary}")
        return "\n".join(lines)


class WatchdogAgent:
    """Evaluate thesis health using AI."""

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def assess(
        self,
        ctx: WatchdogContext,
        session: AsyncSession | None = None,
        user_id: str | None = None,
        trigger: str = "watchdog",
    ) -> ThesisHealthScore | None:
        """Assess thesis health. Returns None on any failure (graceful degrade).

        Args:
            ctx:      WatchdogContext built by WatchdogService.
            session:  Optional AsyncSession. When provided, investor profile +
                      memory context are injected into the prompt.
            user_id:  Optional user_id for memory logging.
            trigger:  Trigger label for episodic log.
        """
        try:
            investor_profile = await self._build_investor_profile(session, user_id)
            user_prompt = build_user_prompt(ctx, investor_profile=investor_profile)
            api_resp = await self._client.chat_completion(
                messages=[
                    {\"role\": \"system\", \"content\": SYSTEM_PROMPT},
                    {\"role\": \"user\",   \"content\": user_prompt},
                ],
                temperature=0.2,
            )
            raw = self._client.extract_text(api_resp)
            data = json.loads(raw)
            result = ThesisHealthScore(**data)
            logger.info(
                "watchdog.assessed",
                thesis_id=ctx.thesis_id,
                ticker=ctx.ticker,
                overall_health=result.overall_health,
                health_score=result.health_score,
                alert_level=result.alert_level,
            )
            # --- Memory: log interaction (Layer 2) ---
            await self._log_interaction(
                session=session,
                user_id=user_id,
                ctx=ctx,
                result=result,
                trigger=trigger,
            )
            return result
        except Exception as exc:
            logger.warning(
                "watchdog.assessment_failed",
                thesis_id=ctx.thesis_id,
                ticker=ctx.ticker,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_investor_profile(self, session, user_id: str | None) -> str:
        """Build investor profile block via ContextBuilder (includes memory).

        Returns empty string when session is None or any error occurs.
        Owner of assembly logic: ai.ContextBuilder.
        """
        if session is None:
            return ""
        try:
            from src.ai.context_builder import ContextBuilder, render_for_agent

            ctx = await ContextBuilder(session).build(user_id=user_id)
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning("watchdog_agent.investor_profile_failed", error=str(exc))
            return ""

    async def _log_interaction(
        self,
        session,
        user_id: str | None,
        ctx: WatchdogContext,
        result: ThesisHealthScore,
        trigger: str,
    ) -> None:
        """Fire-and-forget memory log. Never raises."""
        if session is None or not user_id:
            return
        try:
            from src.ai.memory.memory_service import InteractionEntry, MemoryService

            threatened = [
                t.description[:100]
                for t in result.threatened_assumptions
                if t.is_threatened
            ]
            entry = InteractionEntry(
                user_id=user_id,
                agent_type="watchdog",
                trigger=trigger,
                tickers=[ctx.ticker],
                ai_verdict=f"{result.overall_health} ({result.health_score}/100)",
                ai_confidence=None,
                ai_key_points=result.summary or None,
                ai_risk_signals="\n".join(threatened) if threatened else None,
                thesis_id=ctx.thesis_id,
            )
            await MemoryService.log_interaction(session, entry)
        except Exception as exc:
            logger.warning(
                "watchdog_agent.memory_log_failed",
                ticker=ctx.ticker,
                error=str(exc),
            )
