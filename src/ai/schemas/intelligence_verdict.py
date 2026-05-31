"""
src/ai/schemas/intelligence_verdict.py

Schema for the Intelligence Verdict agent output.
Canonical location — import from src.ai.schemas import VerdictOutput.

Breaking changes from v1:
  - verdict labels unchanged (backward-compat).
  - NEW: conviction (high/medium/low) — replaces raw confidence float as primary
    decision signal. confidence float is kept for downstream numeric scoring.
  - NEW: time_horizon — bao nhiêu lâu verdict này còn giá trị.
  - NEW: thesis_alignment (0.0–1.0) — setup này align với thesis hiện tại không.
  - NEW: key_risk — một rủi ro lớn nhất, viết thẳng.
  - NEW: invalidation_trigger — điều gì sẽ làm verdict này sai.
  - RENAMED: reasoning_summary kept, risk_signals kept, next_watch_items kept.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class VerdictOutput(BaseModel):
    # ------------------------------------------------------------------
    # Core verdict
    # ------------------------------------------------------------------
    verdict: Literal[
        "BUY_SIGNAL",
        "SELL_SIGNAL",
        "HOLD",
        "REVIEW_THESIS",
        "RISK_ALERT",
        "NO_ACTION",
    ] = Field(
        ...,
        description="Nhận định hành động chính.",
    )

    conviction: Literal["high", "medium", "low"] = Field(
        ...,
        description=(
            "Mức độ tin tưởng vào verdict. "
            "high = có nhiều tín hiệu hội tụ, data rõ ràng. "
            "medium = có cơ sở nhưng còn một số ẩn số. "
            "low = giả thuyết suy luận, cần thêm dữ liệu."
        ),
    )

    time_horizon: Literal[
        "intraday",
        "swing_3_5d",
        "position_2_4w",
        "core_3m_plus",
    ] = Field(
        ...,
        description="Khoảng thời gian verdict này còn giá trị.",
    )

    # ------------------------------------------------------------------
    # Thesis context
    # ------------------------------------------------------------------
    thesis_alignment: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ...,
        description=(
            "Mức độ setup hiện tại align với thesis đã xây. "
            "1.0 = hoàn toàn xác nhận thesis. "
            "0.0 = mâu thuẫn trực tiếp. "
            "N/A khi chưa có thesis → đặt 0.5."
        ),
    )

    # ------------------------------------------------------------------
    # Risk + invalidation (bắt buộc, không được để chung chung)
    # ------------------------------------------------------------------
    key_risk: str = Field(
        ...,
        min_length=10,
        max_length=200,
        description=(
            "Một rủi ro lớn nhất cụ thể từ dữ liệu. "
            "Viết thẳng, không chung chung. "
            "VD: 'Cầu thấp bất thường dấu hiệu distribution' chứ không phải 'rủi ro thị trường'."
        ),
    )

    invalidation_trigger: str = Field(
        ...,
        min_length=10,
        max_length=200,
        description=(
            "Điều kiện cụ thể nào sẽ làm verdict này sai. "
            "Bắt đầu bằng: 'Verdict này sai khi...' "
            "Phải đo lường được: giá, khối lượng, ngưỡng % cụ thể."
        ),
    )

    # ------------------------------------------------------------------
    # Action + reasoning
    # ------------------------------------------------------------------
    action: str = Field(
        ...,
        max_length=120,
        description=(
            "Câu lệnh hành động cụ thể. Bắt đầu bằng động từ tiếng Việt. "
            "VD: 'Mua vào 30% vị thế tại vùng 48-49k, SL 46.5k' "
            "hoặc 'Không hành động — chờ xác nhận breakout'."
        ),
    )

    reasoning_summary: str = Field(
        ...,
        max_length=400,
        description="1–3 câu giải thích tại sao chọn verdict này. Dẫn dữ liệu cụ thể.",
    )

    # ------------------------------------------------------------------
    # Supporting lists
    # ------------------------------------------------------------------
    risk_signals: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Tối đa 5 yếu tố rủi ro cụ thể từ dữ liệu, không chung chung.",
    )

    next_watch_items: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Tối đa 5 ticker hoặc sự kiện cần theo dõi tiếp theo.",
    )

    sources: list[str] = Field(
        default_factory=list,
        description="Nguồn dữ liệu được sử dụng.",
    )

    # ------------------------------------------------------------------
    # Legacy numeric confidence (kept for downstream scoring / analytics)
    # ------------------------------------------------------------------
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        default=0.5,
        description=(
            "Numeric confidence 0.0–1.0 — dùng cho analytics/scoring. "
            "Không dùng để hiển thị trực tiếp cho user; dùng conviction thay."
        ),
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("thesis_alignment", mode="before")
    @classmethod
    def _clamp_thesis_alignment(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))
