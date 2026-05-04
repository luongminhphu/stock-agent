"""ReplayAgent — analyze past investor decisions and generate learning feedback.

Owner: ai segment.
Consumed by: thesis.decision_service.DecisionService

Boundary:
- Accepts ReplayContext only.
- Returns DecisionReplayResult only.
- Does not write DB, does not load repository, does not dispatch notifications.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from src.ai.client import AIClient
from src.ai.prompts.replay import ReplayContext, SYSTEM_PROMPT, build_user_prompt
from src.platform.logging import get_logger

logger = get_logger(__name__)


class DecisionReplayResult(BaseModel):
    decision_id: int
    ticker: str
    decision_type: str = Field(..., description="BUY | SELL | HOLD | ADD | REDUCE")
    outcome_verdict: str = Field(..., description="CORRECT | INCORRECT | MIXED")
    what_went_right: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    key_lesson: str
    pattern_detected: str | None = None
    suggested_adjustment: str | None = None
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")


class ReplayAgent:
    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def analyze(self, ctx: ReplayContext) -> DecisionReplayResult | None:
        try:
            raw = await self._client.complete(
                system=SYSTEM_PROMPT,
                user=build_user_prompt(ctx),
                temperature=0.2,
            )
            result = DecisionReplayResult(**json.loads(raw))
            logger.info(
                "decision_replay.analyzed",
                decision_id=result.decision_id,
                ticker=result.ticker,
                verdict=result.outcome_verdict,
            )
            return result
        except Exception as exc:
            logger.warning(
                "decision_replay.analysis_failed",
                decision_id=ctx.decision_id,
                ticker=ctx.ticker,
                error=str(exc),
            )
            return None
