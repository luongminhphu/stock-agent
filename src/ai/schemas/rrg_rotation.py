"""Schema for RRGRotationAgent output.

Owner: ai segment.
Downstream: api/routes/rrg.py → FE rrg-chart.js popup.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RRGRotationSignal(BaseModel):
    """AI rotation analysis for a single ticker on the RRG."""

    ticker: str

    # Current position
    quadrant: str = Field(
        description="leading | weakening | lagging | improving"
    )

    # Movement pattern detected from trail
    pattern: str = Field(
        description=(
            "ENTERING_LEADING | EXITING_LEADING | "
            "ENTERING_IMPROVING | DEEP_LAGGING | "
            "WEAKENING_FAST | RECOVERY | ROTATING | STABLE"
        )
    )

    # Core signals — Option C focus
    signal: str = Field(
        description="BUY | WATCH | HOLD | REDUCE | AVOID"
    )
    signal_reason: str = Field(
        description="1 câu lý do ngắn gọn cho signal, tối đa 120 ký tự"
    )

    # Rotation opportunity detail
    opportunity: str = Field(
        default="",
        description=(
            "Mô tả cơ hội rotation cụ thể nếu có: "
            "ticker đang cross quadrant nào, momentum đang tăng/giảm ra sao"
        ),
    )

    # Risk
    risk: str = Field(
        default="",
        description="Rủi ro chính cần theo dõi với ticker này trong RRG context"
    )

    # What to watch next
    next_watch: str = Field(
        default="",
        description="Điều kiện hoặc mốc kỹ thuật cần theo dõi tiếp theo"
    )

    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("signal", mode="before")
    @classmethod
    def normalise_signal(cls, v: object) -> str:
        mapping = {
            "STRONG_BUY": "BUY", "ACCUMULATE": "BUY", "LONG": "BUY",
            "MONITOR": "WATCH", "CAUTION": "WATCH",
            "NEUTRAL": "HOLD",
            "SELL": "REDUCE", "TRIM": "REDUCE", "DISTRIBUTE": "REDUCE",
            "STRONG_SELL": "AVOID", "EXIT": "AVOID", "SHORT": "AVOID",
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

    @field_validator("signal_reason", "opportunity", "risk", "next_watch", mode="before")
    @classmethod
    def coerce_str(cls, v: object) -> str:
        if isinstance(v, list):
            return " | ".join(str(i) for i in v)
        return str(v) if v is not None else ""
