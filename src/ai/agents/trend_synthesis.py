"""TrendSynthesisAgent — AI verdict from RRG position + technical indicators.

Owner: ai segment.
Caller: market.trend_synthesis_service.TrendSynthesisService

Input:
    - ticker, rrg data (quadrant, rs_ratio, rs_momentum, trail pattern)
    - raw_indicators: rsi, macd_line, macd_signal, macd_hist, macd_cross,
                      cmf, adx, adx_plus_di, adx_minus_di
    - regime, composite score

Output: TrendSynthesisOutput

Boundary rules:
    - No DB access. No market data fetch.
    - Receives pre-computed primitives — pure AI reasoning.
    - Falls back to rule-based verdict when LLM fails.
    - Cache TTL: 300s (same session data doesn't change within 5 min).
"""

from __future__ import annotations

from typing import Any

from src.ai.client import AIClient, AIError
from src.ai.prompt_cache import PromptCache
from src.ai.schemas.trend_synthesis import TrendSynthesisOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)

_MAX_TOKENS = 1024

_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích kỹ thuật thị trường chứng khoán Việt Nam.
Nhiệm vụ: đọc tổ hợp tín hiệu kỹ thuật và trả về JSON theo đúng schema bên dưới.

## Schema bắt buộc (không thêm, không đổi tên field)
{
  "ticker": "<string>",
  "verdict": "BULLISH | NEUTRAL | BEARISH",
  "action": "ACCUMULATE | HOLD | REDUCE | AVOID",
  "confidence": <0.0 đến 1.0>,
  "signal_summary": "<tối đa 200 ký tự — nêu ≥2 indicator làm căn cứ>",
  "rrg_note": "<tối đa 120 ký tự>",
  "macd_note": "<tối đa 120 ký tự>",
  "rsi_note": "<tối đa 100 ký tự>",
  "cmf_note": "<tối đa 100 ký tự>",
  "adx_note": "<tối đa 100 ký tự>",
  "next_watch": "<tối đa 150 ký tự>"
}

## Cách đọc indicators
- **RRG**: tương đối so VNINDEX. Leading/Improving = mạnh hơn thị trường
- **RSI**: overbought >70, oversold <30
- **MACD histogram**: đơn vị VND. Dương = bullish pressure, âm = bearish
- **CMF**: -1 đến +1. Dương = dòng tiền vào, âm = dòng tiền ra
- **ADX**: <20 = ranging, 20-40 = trending, >40 = strong trend. +DI > -DI = uptrend

## Verdict logic
- BULLISH + ACCUMULATE: Leading/Improving + MACD bullish + CMF > 0.05 + ADX > 20
- NEUTRAL + HOLD: tín hiệu hỗn hợp, thiếu xác nhận ≥2 indicator
- BEARISH + REDUCE: Weakening/Lagging + MACD bearish + CMF < -0.05
- BEARISH + AVOID: Lagging + ADX > 30 + CMF âm mạnh

## Quy tắc cứng
- Chỉ dùng đúng các field trong schema trên — không thêm field mới
- ADX < 20 → adx_note phải có "không có trend rõ"
- verdict và action không được mâu thuẫn
- Trả về JSON hợp lệ, không markdown, không giải thích ngoài JSON
"""

# Module-level cache — TTL 1800s (30 min).
# Daily OHLCV-based indicators don’t change within a trading session.
# Route-level DashboardTTLCache is the primary hit path (ticker-keyed).
# This PromptCache acts as secondary guard against repeated AI calls
# when route cache misses (e.g. after process restart).
_cache: PromptCache[TrendSynthesisOutput] = PromptCache(
    ttl_seconds=1800,
    agent_name="trend_synthesis",
)


class TrendSynthesisAgent:
    """Synthesise RRG + technical indicators into an actionable trend verdict."""

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def run(self, payload: dict[str, Any]) -> TrendSynthesisOutput:
        """Run synthesis for one ticker.

        Args:
            payload: dict with keys — ticker, rrg, raw_indicators, regime, composite
        Returns:
            TrendSynthesisOutput (AI or rule-based fallback)
        """
        ticker = payload.get("ticker", "UNKNOWN")
        user_prompt = self._build_prompt(payload)

        cached = _cache.get(_SYSTEM_PROMPT, user_prompt, TrendSynthesisOutput)
        if cached is not None:
            return cached

        try:
            result = await self._client.chat(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=TrendSynthesisOutput,
                max_tokens=_MAX_TOKENS,
            )
            _cache.set(_SYSTEM_PROMPT, user_prompt, result)
            return result

        except (AIError, Exception) as exc:
            logger.warning(
                "trend_synthesis.ai_failed",
                ticker=ticker,
                error=str(exc),
            )
            return self._rule_based_fallback(payload)

    def _build_prompt(self, payload: dict[str, Any]) -> str:
        ticker = payload.get("ticker", "?")
        rrg = payload.get("rrg", {})
        ind = payload.get("raw_indicators", {})
        regime = payload.get("regime", "UNKNOWN")
        composite = payload.get("composite", 0.5)

        return f"""Phân tích xu hướng cho mã **{ticker}**:

## RRG Position
- Quadrant: {rrg.get('quadrant', 'unknown')}
- RS-Ratio: {rrg.get('rs_ratio', 100):.2f} (>100 = mạnh hơn VNINDEX)
- RS-Momentum: {rrg.get('rs_momentum', 100):.2f} (>100 = momentum tăng)
- Trail pattern: {rrg.get('trail_pattern', 'N/A')}

## Technical Indicators
- RSI(14): {ind.get('rsi', 50):.1f}
- MACD Histogram: {ind.get('macd_hist', 0):.4f} | Signal: {ind.get('macd_cross', 'N/A')}
- CMF(20): {ind.get('cmf', 0):.4f}
- ADX(14): {ind.get('adx', 0):.1f} | +DI: {ind.get('adx_plus_di', 0):.1f} | -DI: {ind.get('adx_minus_di', 0):.1f}

## Composite
- Regime: {regime}
- Composite score: {composite:.2f} (0=rất bearish, 1=rất bullish)

Trả về JSON theo schema TrendSynthesisOutput."""

    @staticmethod
    def _rule_based_fallback(payload: dict[str, Any]) -> TrendSynthesisOutput:
        """Deterministic fallback when LLM unavailable."""
        ticker = payload.get("ticker", "UNKNOWN")
        rrg = payload.get("rrg", {})
        ind = payload.get("raw_indicators", {})
        composite = float(payload.get("composite", 0.5))

        quadrant = rrg.get("quadrant", "unknown")
        rsi = float(ind.get("rsi", 50))
        cmf = float(ind.get("cmf", 0))
        adx = float(ind.get("adx", 0))
        macd_cross = ind.get("macd_cross", "bearish_cross")

        # Scoring: đếm tín hiệu bullish vs bearish
        bull = 0
        bear = 0

        if quadrant in ("leading", "improving"):
            bull += 2
        elif quadrant in ("lagging", "weakening"):
            bear += 2

        if macd_cross == "bullish_cross":
            bull += 1
        else:
            bear += 1

        if cmf > 0.05:
            bull += 1
        elif cmf < -0.05:
            bear += 1

        if rsi < 30:
            bull += 1  # oversold — potential reversal
        elif rsi > 70:
            bear += 1  # overbought

        if bull > bear + 1:
            verdict, action = "BULLISH", "ACCUMULATE"
        elif bear > bull + 1:
            verdict = "BEARISH"
            action = "AVOID" if quadrant == "lagging" and adx > 25 else "REDUCE"
        else:
            verdict, action = "NEUTRAL", "HOLD"

        adx_note = (
            f"ADX {adx:.0f} — không có trend rõ, cẩn thận tín hiệu giả."
            if adx < 20
            else f"ADX {adx:.0f} — trend {'mạnh' if adx > 35 else 'đang hình thành'}."
        )

        return TrendSynthesisOutput(
            ticker=ticker,
            verdict=verdict,
            action=action,
            confidence=min(0.6, 0.3 + abs(bull - bear) * 0.1),
            signal_summary=(
                f"Rule-based: RRG {quadrant}, MACD {macd_cross.replace('_', ' ')}, "
                f"CMF {cmf:+.3f}. Composite {composite:.2f}."
            ),
            rrg_note=f"RRG quadrant: {quadrant}. RS-Ratio {rrg.get('rs_ratio', 100):.1f}.",
            macd_note=f"MACD {macd_cross.replace('_', ' ')}, histogram {ind.get('macd_hist', 0):.2f}.",
            rsi_note=f"RSI {rsi:.0f}{'— overbought.' if rsi > 70 else '— oversold.' if rsi < 30 else '.'}",
            cmf_note=f"CMF {cmf:+.3f} — {'buying' if cmf > 0 else 'selling'} pressure.",
            adx_note=adx_note,
            next_watch="Theo dõi xác nhận từ volume và MACD cross tiếp theo.",
        )
