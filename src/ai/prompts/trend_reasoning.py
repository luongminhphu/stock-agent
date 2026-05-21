"""Trend reasoning prompt pack + TrendPrediction schema.

Owner: ai segment.
Input:  TechnicalSignalBundle (from market segment)
Output: TrendPrediction (structured, stable for bot/api/briefing downstream)
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TrendPrediction(BaseModel):
    symbol: str
    verdict: Literal["STRONG_BUY", "BUY", "HOLD", "WATCH", "REDUCE", "STRONG_SELL"]
    direction: Literal["UP", "DOWN", "SIDEWAYS"]
    confidence: float = Field(ge=0.0, le=0.85)  # hard cap — avoid overconfidence
    horizon: Literal["SHORT_TERM", "MID_TERM"]  # SHORT=1-5d, MID=2-4w
    risk_signals: list[str] = Field(default_factory=list)
    next_watch: list[str]   = Field(default_factory=list)
    reasoning: str          = Field(default="", max_length=200)
    generated_at: datetime  = Field(default_factory=datetime.utcnow)
    is_stale: bool          = False  # set by consumer if age > 4h


TREND_SYSTEM_PROMPT = """\
Bạn là AI phân tích kỹ thuật chứng khoán Việt Nam (HOSE/HNX/UPCoM).
Nhiệm vụ: đọc tín hiệu kỹ thuật và đưa ra verdict xu hướng ngắn/trung hạn.

Quy tắc cứng:
- Không bao giờ dự đoán giá cụ thể (price target).
- confidence tối đa 0.85.
- risk_signals phải honest, không che giấu rủi ro.
- reasoning tối đa 200 ký tự.
- Verdict STRONG_BUY/STRONG_SELL chỉ khi composite > 0.75 hoặc < 0.25.
- Trả về JSON hợp lệ theo schema TrendPrediction, không thêm field khác.
"""

TREND_USER_TEMPLATE = """\
Symbol: {symbol}
Regime: {regime}
Composite score: {composite:.2f}

Tín hiệu kỹ thuật:
- Momentum  ({momentum_label}): {momentum_val:.2f}
- Structure ({structure_label}): {structure_val:.2f}
- Volume    ({volume_label}): {volume_val:.2f}
- Volatility({volatility_label}): {volatility_val:.2f}

Thesis (nếu có): {thesis_summary}

Hãy trả về JSON hợp lệ theo schema TrendPrediction.
"""


def build_trend_prompt(bundle, thesis_summary: str = "N/A") -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for LLM call.

    Args:
        bundle: TechnicalSignalBundle from market segment
        thesis_summary: optional thesis context string
    """
    user = TREND_USER_TEMPLATE.format(
        symbol=bundle.symbol,
        regime=bundle.regime,
        composite=bundle.composite,
        momentum_label=bundle.momentum.label,
        momentum_val=bundle.momentum.value,
        structure_label=bundle.structure.label,
        structure_val=bundle.structure.value,
        volume_label=bundle.volume.label,
        volume_val=bundle.volume.value,
        volatility_label=bundle.volatility.label,
        volatility_val=bundle.volatility.value,
        thesis_summary=thesis_summary,
    )
    return TREND_SYSTEM_PROMPT, user
