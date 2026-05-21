"""
TrendReasoningAgent — AI verdict for trend prediction.

Owner: ai segment.
Consumer: ai.TrendEngineListener (via TrendEngine pipeline).

Boundary:
    - Accepts TechnicalSignalBundle (market segment DTO) + thesis_context str.
    - Returns TrendPrediction (defined here, canonical for this agent).
    - Does NOT write DB. Does NOT import thesis/watchlist repositories.
    - Memory logging is optional (session=None → skipped gracefully).

Client note:
    Uses client.chat() — NEVER chat_completion() with response_format.
    sonar-pro rejects {"type": "json_object"} with HTTP 400 (same as StressTestAgent).

Prompt pack:
    System prompt + build_user_prompt() from src.ai.prompts.trend_reasoning.
    User prompt embeds schema_example JSON block to anchor AI output format.

Schema:
    TrendPrediction is defined here (not in src.ai.schemas) because this is
    the first consumer. Move to src.ai.schemas if a second consumer appears.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from src.ai.client import AIClient, AIError
from src.ai.prompts.trend_reasoning import SYSTEM_PROMPT, build_user_prompt
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.market.trend_engine import TechnicalSignalBundle

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class TrendPrediction(BaseModel):
    """AI verdict for a single symbol's trend direction.

    Produced by TrendReasoningAgent.analyze().
    Consumed by TrendPredictionStore, TrendEngineListener,
    BriefingListener (top verdicts), and bot Discord embed.
    """
    symbol: str
    verdict: Literal["STRONG_BUY", "BUY", "HOLD", "WATCH", "REDUCE", "STRONG_SELL"]
    direction: Literal["UP", "DOWN", "SIDEWAYS"]
    confidence: float = Field(ge=0.0, le=1.0)
    horizon: Literal["SHORT_TERM", "MID_TERM"]  # SHORT_TERM: 1-5 ngày, MID_TERM: 2-4 tuần
    risk_signals: list[str] = Field(default_factory=list)   # ≤ 4 items
    next_watch: list[str] = Field(default_factory=list)     # ≤ 4 items
    reasoning: str = ""                                      # ≤ 120 chars
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_actionable(self) -> bool:
        """True if verdict warrants immediate attention."""
        return self.verdict in ("STRONG_BUY", "BUY", "REDUCE", "STRONG_SELL")

    @property
    def is_stale(self) -> bool:
        """True if prediction is older than 4 hours."""
        age = (datetime.now(UTC) - self.generated_at).total_seconds()
        return age > 14400


# ---------------------------------------------------------------------------
# Fallback factory
# ---------------------------------------------------------------------------

def _fallback_prediction(symbol: str, composite: float) -> TrendPrediction:
    """Return a rule-based fallback when LLM call fails.

    Uses composite score directly — no AI reasoning.
    Confidence is capped at 0.50 to signal lower certainty.
    """
    if composite >= 0.60:
        verdict, direction = "BUY", "UP"
    elif composite <= 0.40:
        verdict, direction = "REDUCE", "DOWN"
    else:
        verdict, direction = "HOLD", "SIDEWAYS"

    return TrendPrediction(
        symbol=symbol,
        verdict=verdict,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        confidence=min(composite, 0.50),
        horizon="SHORT_TERM",
        risk_signals=["AI unavailable — rule-based fallback"],
        next_watch=[],
        reasoning=f"Fallback: composite={composite:.2f}, no AI reasoning.",
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class TrendReasoningAgent:
    """Generate AI trend verdict from TechnicalSignalBundle.

    Follows StressTestAgent pattern:
    - __init__(client: AIClient)
    - client.chat(system_prompt, user_prompt, response_schema, temperature)
    - Optional memory logging via MemoryService
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def analyze(
        self,
        bundle: "TechnicalSignalBundle",
        thesis_context: str = "N/A",
        session: object | None = None,
        user_id: str | None = None,
    ) -> TrendPrediction:
        """Analyze a TechnicalSignalBundle and return a TrendPrediction.

        Falls back to rule-based verdict if LLM call fails —
        never raises, never blocks the event pipeline.
        """
        symbol = bundle.symbol

        user_prompt = build_user_prompt(
            symbol=symbol,
            regime=bundle.regime,
            composite=bundle.composite,
            momentum_label=bundle.momentum.label,
            momentum_value=bundle.momentum.value,
            structure_label=bundle.structure.label,
            structure_value=bundle.structure.value,
            volume_label=bundle.volume.label,
            volume_value=bundle.volume.value,
            volatility_label=bundle.volatility.label,
            volatility_value=bundle.volatility.value,
            thesis_context=thesis_context,
            as_of=bundle.as_of.strftime("%Y-%m-%d %H:%M") if bundle.as_of else "",
        )

        logger.info(
            "trend_reasoning_agent.analyze.start",
            symbol=symbol,
            regime=bundle.regime,
            composite=bundle.composite,
        )

        try:
            result = await self._client.chat(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=TrendPrediction,
                temperature=0.15,
            )
        except AIError as exc:
            logger.warning(
                "trend_reasoning_agent.analyze.ai_error",
                symbol=symbol,
                error=str(exc),
            )
            return _fallback_prediction(symbol, bundle.composite)
        except Exception as exc:
            logger.error(
                "trend_reasoning_agent.analyze.unexpected_error",
                symbol=symbol,
                error=str(exc),
            )
            return _fallback_prediction(symbol, bundle.composite)

        # Ensure symbol is set correctly (LLM may hallucinate wrong symbol)
        if result.symbol.upper() != symbol.upper():
            logger.warning(
                "trend_reasoning_agent.analyze.symbol_mismatch",
                expected=symbol,
                got=result.symbol,
            )
            result = result.model_copy(update={"symbol": symbol.upper()})

        logger.info(
            "trend_reasoning_agent.analyze.complete",
            symbol=symbol,
            verdict=result.verdict,
            direction=result.direction,
            confidence=result.confidence,
            horizon=result.horizon,
        )

        await self._log_interaction(session, user_id, symbol, result)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _log_interaction(
        self,
        session: object | None,
        user_id: str | None,
        symbol: str,
        result: TrendPrediction,
    ) -> None:
        """Log prediction to memory service. No-op if session is None."""
        if session is None or not user_id:
            return
        try:
            from src.ai.memory.memory_service import InteractionEntry, MemoryService

            entry = InteractionEntry(
                user_id=user_id,
                agent_type="trend_reasoning",
                trigger="trend_engine",
                tickers=[symbol],
                ai_verdict=result.verdict,
                ai_confidence=result.confidence,
                ai_key_points=result.reasoning[:300] if result.reasoning else None,
                ai_risk_signals="\n".join(result.risk_signals[:4]) if result.risk_signals else None,
            )
            await MemoryService.log_interaction(session, entry)
        except Exception as exc:
            logger.warning(
                "trend_reasoning_agent.memory_log_failed",
                symbol=symbol,
                error=str(exc),
            )
