"""Schema for TrendSynthesisAgent output.

Owner: ai segment.
Downstream: api/routes/trend.py → FE trend panel in thesis detail sidebar.

Combines:
  - RRG position (quadrant, rs_ratio, rs_momentum, trail pattern)
  - Technical indicators (RSI, MACD, CMF, ADX)
  - AI verdict (action + reasoning)
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TrendSynthesisOutput(BaseModel):
    """AI synthesis of RRG + MACD/RSI/CMF/ADX for a single ticker."""

    ticker: str

    # ── Verdict ──────────────────────────────────────────────────────────────
    verdict: str = Field(
        description="BULLISH | NEUTRAL | BEARISH",
    )
    action: str = Field(
        description="ACCUMULATE | HOLD | REDUCE | AVOID",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    # ── Signal summary ────────────────────────────────────────────────────────
    signal_summary: str = Field(
        description=(
            "1-2 câu tóm tắt lý do verdict, tối đa 200 ký tự. "
            "Phải nêu được: momentum, money flow và trend strength."
        ),
    )

    # ── Per-indicator interpretation ──────────────────────────────────────────
    rrg_note: str = Field(
        default="",
        description="Nhận xét ngắn về vị trí RRG và trajectory, tối đa 120 ký tự",
    )
    macd_note: str = Field(
        default="",
        description="Nhận xét về MACD histogram và crossover signal, tối đa 120 ký tự",
    )
    rsi_note: str = Field(
        default="",
        description="Nhận xét về RSI — overbought/oversold/neutral, tối đa 100 ký tự",
    )
    cmf_note: str = Field(
        default="",
        description="Nhận xét về money flow (CMF), tối đa 100 ký tự",
    )
    adx_note: str = Field(
        default="",
        description="Nhận xét về trend strength (ADX) và direction (+DI vs -DI), tối đa 100 ký tự",
    )

    # ── Watch conditions ──────────────────────────────────────────────────────
    next_watch: str = Field(
        default="",
        description="Điều kiện kỹ thuật cần theo dõi tiếp theo, tối đa 150 ký tự",
    )

    @field_validator("verdict", mode="before")
    @classmethod
    def normalise_verdict(cls, v: object) -> str:
        mapping = {
            "STRONG_BULLISH": "BULLISH",
            "STRONG_BEARISH": "BEARISH",
            "SIDEWAYS": "NEUTRAL",
            "RANGING": "NEUTRAL",
        }
        s = str(v).upper().strip()
        return mapping.get(s, s)

    @field_validator("action", mode="before")
    @classmethod
    def normalise_action(cls, v: object) -> str:
        mapping = {
            "BUY": "ACCUMULATE",
            "STRONG_BUY": "ACCUMULATE",
            "SELL": "REDUCE",
            "STRONG_SELL": "AVOID",
            "NEUTRAL": "HOLD",
            "WATCH": "HOLD",
        }
        s = str(v).upper().strip()
        return mapping.get(s, s)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: object) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
            if f > 1.0:
                f /= 10.0
            return max(0.0, min(1.0, f))
        except (TypeError, ValueError):
            return 0.5

    @field_validator(
        "signal_summary", "rrg_note", "macd_note", "rsi_note",
        "cmf_note", "adx_note", "next_watch",
        mode="before",
    )
    @classmethod
    def coerce_str(cls, v: object) -> str:
        if isinstance(v, list):
            return " | ".join(str(i) for i in v)
        return str(v) if v is not None else ""

    @field_validator("signal_summary", mode="after")
    @classmethod
    def truncate_signal_summary(cls, v: str) -> str:
        return v[:200] if len(v) > 200 else v

    @field_validator("rrg_note", "macd_note", mode="after")
    @classmethod
    def truncate_120(cls, v: str) -> str:
        return v[:120] if len(v) > 120 else v

    @field_validator("rsi_note", "cmf_note", "adx_note", mode="after")
    @classmethod
    def truncate_100(cls, v: str) -> str:
        return v[:100] if len(v) > 100 else v

    @field_validator("next_watch", mode="after")
    @classmethod
    def truncate_150(cls, v: str) -> str:
        return v[:150] if len(v) > 150 else v
