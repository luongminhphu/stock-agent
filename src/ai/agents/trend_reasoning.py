"""TrendReasoningAgent — AI agent that generates TrendPrediction from signals.

Owner: ai segment.
Input:  TechnicalSignalBundle (from market segment, passed as-is)
Output: TrendPrediction

Boundary:
  - NEVER imports from briefing, thesis, or bot segments.
  - Receives TechnicalSignalBundle as a typed object (imported from
    ai.schemas.trend_prediction — both live in ai segment).
  - Falls back to market.trend_engine.rule_based_prediction() when the LLM
    call fails — the technical rule is owned by market, not by this agent
    (Wave F8b: gỡ bản rule thứ hai từng lệch ngưỡng với bot /trend).

Prompt design:
  - Bundle labels (BULLISH/NEUTRAL/BEARISH) are sent, not raw prices.
    This prevents the LLM from hallucinating specific price targets.
  - thesis_context is optional ("N/A" when not available).
  - system_prompt defines role + output contract (stable across requests).
  - user_prompt carries per-request signal data (changes every call).

Fallback (non-blocking):
  When LLM call fails or returns unparseable output, fallback() returns
  market.rule_based_prediction(bundle) — confidence capped at 0.5, reasoning
  tagged "Rule-based fallback".
  This ensures TrendEngineListener.results never contains Exceptions
  from reasoning failures — only from catastrophic signal failures.
"""

from __future__ import annotations

from src.ai.schemas.trend_prediction import (
    TechnicalSignalBundle,
    TrendPrediction,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
Bạn là AI phân tích kỹ thuật cho thị trường chứng khoán Việt Nam.
Dựa trên dữ liệu kỹ thuật được cung cấp, hãy đưa ra dự đoán xu hướng.

You MUST return a JSON object with EXACTLY these fields — no extra fields:
{{
  "symbol": "<ticker>",
  "verdict": "<STRONG_BUY|BUY|HOLD|WATCH|REDUCE|STRONG_SELL>",
  "direction": "<UP|DOWN|SIDEWAYS>",
  "confidence": <float 0.0-0.85>,
  "horizon": "<SHORT_TERM|MID_TERM>",
  "risk_signals": ["<tối đa 4 risk ngắn gọn>"],
  "next_watch": ["<tối đa 3 trigger cần theo dõi>"],
  "reasoning": "<1 câu tóm tắt, tối đa 200 ký tự>"
}}

Quy tắc bắt buộc:
- confidence tối đa 0.85 — không được vượt quá.
- Không đề cập giá cụ thể trong reasoning hoặc next_watch.
- next_watch phải là điều kiện kỹ thuật (ví dụ: "MACD cross confirm", "Break above EMA50").
- Nếu tín hiệu mâu thuẫn, ưu tiên structure và volume hơn momentum đơn thuần.
"""

_USER_PROMPT_TEMPLATE = """\
Phân tích xu hướng cho mã {symbol}.

## Tín hiệu kỹ thuật
- Momentum (RSI/MACD):   {momentum_label} (score: {momentum_value:.2f})
- Structure (EMA/Swing): {structure_label} (score: {structure_value:.2f})
- Volume (OBV/Surge):    {volume_label} (score: {volume_value:.2f})
- Volatility (ATR):      {volatility_label} (score: {volatility_value:.2f})
- Composite score:       {composite:.2f}
- Regime:                {regime}

## Thesis context
{thesis_context}
"""


class TrendReasoningAgent:
    """Calls LLM to produce TrendPrediction from TechnicalSignalBundle.

    Args:
        client:      AIClient instance (src/ai/client.py).
        model:       LLM model override. None → uses AIClient.DEFAULT_MODEL.
        temperature: Sampling temperature. Lower = more deterministic.
    """

    def __init__(
        self,
        client: object,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature

    async def analyze(
        self,
        bundle: TechnicalSignalBundle,
        thesis_context: str = "N/A",
    ) -> TrendPrediction:
        """Run LLM reasoning on a TechnicalSignalBundle.

        Returns TrendPrediction on success.
        Falls back to fallback() on any LLM or parse failure.
        Never raises — callers can always unpack the result.

        Args:
            bundle:         TechnicalSignalBundle from market.TrendEngine.
            thesis_context: Optional thesis summary string for context.
                            Pass \"N/A\" when unavailable.
        """
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            symbol=bundle.symbol,
            momentum_label=bundle.momentum.label.value,
            momentum_value=bundle.momentum.value,
            structure_label=bundle.structure.label.value,
            structure_value=bundle.structure.value,
            volume_label=bundle.volume.label.value,
            volume_value=bundle.volume.value,
            volatility_label=bundle.volatility.label.value,
            volatility_value=bundle.volatility.value,
            composite=bundle.composite,
            regime=bundle.regime.value,
            thesis_context=thesis_context or "N/A",
        )

        try:
            prediction = await self._client.chat(  # type: ignore[attr-defined]
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=TrendPrediction,
                model=self._model,
                temperature=self._temperature,
            )
            return prediction
        except Exception as exc:
            logger.warning(
                "trend_reasoning_agent.llm_failed",
                symbol=bundle.symbol,
                error=str(exc),
            )
            return self.fallback(bundle)

    # -----------------------------------------------------------------------
    # Fallback
    # -----------------------------------------------------------------------

    def fallback(self, bundle: TechnicalSignalBundle) -> TrendPrediction:
        """Non-AI verdict — delegates to market.trend_engine.rule_based_prediction.

        Public so briefing.trend_batch_scheduler can call it without reaching
        into a private method. Lazy import: ai → market only at call time.
        """
        from src.market.trend_engine import rule_based_prediction  # noqa: PLC0415

        logger.info("trend_reasoning_agent.fallback_used", symbol=bundle.symbol)
        prediction: TrendPrediction = rule_based_prediction(bundle)
        return prediction
