"""Signal Credibility Agent.

Owner: ai segment.
Consumed by: watchlist.ScanService (optional enrichment step).

Responsibility boundary:
  - Accepts a SignalCredibilityContext, calls AI, returns SignalCredibilityResult.
  - Does NOT write to DB, does NOT trigger alerts, does NOT modify ScanSignal state.
  - Caller (ScanService) is responsible for attaching result to ScanSignal.credibility.

Graceful degrade: if AI call fails, returns None so the scan pipeline is unaffected.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from src.ai.client import AIClient
from src.ai.prompts.signal_credibility import (
    SYSTEM_PROMPT,
    SignalCredibilityContext,
    build_user_prompt,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class SignalCredibilityResult(BaseModel):  # noqa: D101
    score: int = Field(..., ge=0, le=100, description="Credibility score 0–100")
    verdict: str = Field(..., description="STRONG | MODERATE | WEAK | NOISE")
    supporting_factors: list[str] = Field(default_factory=list)
    failure_risks: list[str] = Field(default_factory=list)
    volume_confirmed: bool = False
    trend_aligned: bool = False
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")

    @property
    def is_actionable(self) -> bool:
        """True when signal is worth acting on (STRONG or MODERATE with HIGH/MEDIUM confidence)."""
        if self.verdict == "STRONG":
            return True
        if self.verdict == "MODERATE" and self.confidence in ("HIGH", "MEDIUM"):
            return True
        return False

    def short_summary(self) -> str:
        """One-liner for Discord embeds."""
        icon = {"STRONG": "🟢", "MODERATE": "🟡", "WEAK": "🟠", "NOISE": "🔴"}.get(self.verdict, "⚪")
        return f"{icon} {self.verdict} ({self.score}/100) — {'; '.join(self.failure_risks[:1]) or 'N/A'}"


class SignalCredibilityAgent:
    """Evaluate the credibility of a detected scan signal using AI."""

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def evaluate(
        self, ctx: SignalCredibilityContext
    ) -> SignalCredibilityResult | None:
        """Score the signal. Returns None on any failure (graceful degrade)."""
        try:
            user_prompt = build_user_prompt(ctx)
            raw = await self._client.complete(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                temperature=0.2,
            )
            data = json.loads(raw)
            result = SignalCredibilityResult(**data)
            logger.info(
                "signal_credibility.evaluated",
                ticker=ctx.ticker,
                signal_type=ctx.signal_type,
                verdict=result.verdict,
                score=result.score,
            )
            return result
        except Exception as exc:
            logger.warning(
                "signal_credibility.evaluation_failed",
                ticker=ctx.ticker,
                signal_type=ctx.signal_type,
                error=str(exc),
            )
            return None
