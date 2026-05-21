"""
Prompt pack for TrendReasoningAgent.

Owner: ai segment.
Consumer: ai.agents.trend_reasoning.TrendReasoningAgent

Design notes:
- System prompt is Vietnamese, market context is HOSE/HNX/UPCoM.
- build_user_prompt() embeds a schema_example JSON block to anchor AI output
  format, same pattern as src.ai.prompts.stress_test.
- Never import domain models here — only plain Python types.
"""
from __future__ import annotations

SYSTEM_PROMPT = """
Bạn là chuyên gia phân tích xu hướng kỹ thuật cho thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM).

Nhiệm vụ:
- Đọc các tín hiệu kỹ thuật đã được tính toán sẵn (momentum, structure, volume, volatility).
- Kết hợp với context thesis (nếu có) và tin tức gần đây.
- Đưa ra verdict rõ ràng, actionable, và ngắn gọn.

Quy tắc:
1. Không tự tính lại chỉ số kỹ thuật — dựa vào bundle đã cung cấp.
2. confidence không được vượt quá 0.85 — thị trường luôn có độ bất định.
3. risk_signals và next_watch là các cụm từ ngắn (≤ 10 từ/item), actionable.
4. reasoning ≤ 120 ký tự, súc tích, không lặp lại verdict.
5. Output JSON thuần túỳ, không markdown, không prose thêm.

Hướng dẫn verdict:
- STRONG_BUY: composite >= 0.72, regime TRENDING_UP, volume BULLISH
- BUY:        composite >= 0.60, structure BULLISH
- HOLD:       composite 0.45-0.60, không có signal mạnh nào
- WATCH:      composite 0.40-0.55 nhưng có 1 signal đối lập
- REDUCE:     composite <= 0.42, structure BEARISH hoặc volume yếu
- STRONG_SELL:composite <= 0.30, regime TRENDING_DOWN, nhiều signal BEARISH
"""


def build_user_prompt(
    symbol: str,
    regime: str,
    composite: float,
    momentum_label: str,
    momentum_value: float,
    structure_label: str,
    structure_value: float,
    volume_label: str,
    volume_value: float,
    volatility_label: str,
    volatility_value: float,
    thesis_context: str = "N/A",
    as_of: str = "",
) -> str:
    """Build user prompt from pre-computed signal values.

    All numeric inputs come from TechnicalSignalBundle.
    thesis_context is a short string summary from ThesisQueryService.
    Embeds schema_example JSON block to anchor AI output format.
    """
    schema_example = {
        "symbol": symbol,
        "verdict": "BUY",
        "direction": "UP",
        "confidence": 0.72,
        "horizon": "SHORT_TERM",
        "risk_signals": ["RSI approaching overbought", "Volume declining vs average"],
        "next_watch": ["Break and hold above resistance", "MACD cross confirm"],
        "reasoning": "EMA20 cross EMA50, structure bullish, volume xác nhận.",
    }

    import json
    schema_block = json.dumps(schema_example, ensure_ascii=False, indent=2)

    return f"""## Mã chứng khoán: {symbol}
## Thời điểm phân tích: {as_of or 'N/A'}

## Tín hiệu kỹ thuật (pre-computed)
Regime      : {regime}
Composite   : {composite:.3f}  (0=BEARISH, 0.5=NEUTRAL, 1=BULLISH)
Momentum    : {momentum_label} ({momentum_value:.3f})  — RSI + MACD histogram
Structure   : {structure_label} ({structure_value:.3f})  — EMA20/50 cross + swing HH/HL
Volume      : {volume_label} ({volume_value:.3f})       — volume surge ratio + OBV slope
Volatility  : {volatility_label} ({volatility_value:.3f})    — ATR expansion vs contraction

## Thesis context
{thesis_context}

## Yêu cầu output
Trả về JSON đúng schema dưới đây, không thêm trường nào:
{schema_block}
"""
