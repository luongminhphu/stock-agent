"""Thesis DTOs.

Owner: api segment.
No SQLAlchemy objects cross this boundary.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.thesis.models import AssumptionStatus, CatalystStatus

# ---------------------------------------------------------------------------
# Assumption
# ---------------------------------------------------------------------------


class AssumptionResponse(BaseModel):
    id: int
    thesis_id: int
    description: str
    status: str
    rationale: str | None = None
    confidence: float | None = None
    note: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssumptionListResponse(BaseModel):
    items: list[AssumptionResponse]
    total: int


class AssumptionCreateRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=1000)
    status: AssumptionStatus = AssumptionStatus.PENDING
    note: str | None = Field(default=None, max_length=2000)


class AssumptionUpdateRequest(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    status: AssumptionStatus | None = None
    note: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Catalyst
# ---------------------------------------------------------------------------


class CatalystResponse(BaseModel):
    id: int
    thesis_id: int
    description: str
    status: str
    rationale: str | None = None
    expected_timeline: str | None = None
    expected_date: datetime | None = None
    triggered_at: datetime | None = None
    note: str | None = None

    model_config = {"from_attributes": True}


class CatalystListResponse(BaseModel):
    items: list[CatalystResponse]
    total: int


class CatalystCreateRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=1000)
    status: CatalystStatus = CatalystStatus.PENDING
    expected_date: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class CatalystUpdateRequest(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    status: CatalystStatus | None = None
    expected_date: datetime | None = None
    triggered_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Thesis
# ---------------------------------------------------------------------------


class ThesisResponse(BaseModel):
    id: int
    user_id: str
    ticker: str
    title: str
    summary: str | None = None
    status: str
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    score: float | None = None
    score_tier: str | None = None
    score_tier_icon: str | None = None
    score_breakdown: HealthScoreBreakdown | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    assumptions: list[AssumptionResponse] = []
    catalysts: list[CatalystResponse] = []

    model_config = {"from_attributes": True}


class ThesisListResponse(BaseModel):
    items: list[ThesisResponse]
    total: int


class ThesisCreateRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    title: str = Field(..., min_length=1, max_length=256)
    summary: str = Field(default="", max_length=4000)
    entry_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    assumptions: list[str] = Field(default_factory=list)
    catalysts: list[str] = Field(default_factory=list)


class ThesisUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    summary: str | None = Field(default=None, max_length=4000)
    entry_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)


# ---------------------------------------------------------------------------
# Health Score
# ---------------------------------------------------------------------------


class HealthScoreBreakdown(BaseModel):
    assumption_health: float = Field(..., description="Score contribution from assumptions (0-40)")
    catalyst_progress: float = Field(..., description="Score contribution from catalysts (0-30)")
    risk_reward: float = Field(..., description="Score contribution from R/R ratio (0-20)")
    review_confidence: float = Field(
        ..., description="Score contribution from latest AI review (0-10)"
    )


class HealthScoreResponse(BaseModel):
    thesis_id: int
    total: float = Field(..., description="Composite health score 0-100")
    breakdown: HealthScoreBreakdown


# ---------------------------------------------------------------------------
# Review (existing — kept for backward compat)
# ---------------------------------------------------------------------------


class ThesisReviewResponse(BaseModel):
    """Response for a single ThesisReview record."""

    id: int
    thesis_id: int
    verdict: str
    confidence: float
    reasoning: str
    risk_signals: list[str]
    next_watch_items: list[str]
    reviewed_at: datetime
    reviewed_price: float | None = None

    @field_validator("risk_signals", "next_watch_items", mode="before")
    @classmethod
    def parse_json_list(cls, v: object) -> list[str]:
        """ORM stores these as JSON strings; API exposes them as real lists."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else [v]
            except json.JSONDecodeError:
                return [v]
        if isinstance(v, list):
            return v
        return []

    model_config = {"from_attributes": True}


class ThesisReviewListResponse(BaseModel):
    thesis_id: int
    reviews: list[ThesisReviewResponse]
    total: int


# ---------------------------------------------------------------------------
# AI Recommendations (Wave 1)
# ---------------------------------------------------------------------------


class RecommendationResponse(BaseModel):
    """Response for a single ReviewRecommendation record.

    target_type: "assumption" | "catalyst"
    status:      "pending" | "accepted" | "rejected"
    """

    id: int
    review_id: int
    target_type: str
    target_id: int
    target_description: str
    recommended_status: str
    reason: str
    status: str
    acted_at: datetime | None = None

    model_config = {"from_attributes": True}


class RecommendationListResponse(BaseModel):
    thesis_id: int
    items: list[RecommendationResponse]
    total: int


class ApplyRecommendationRequest(BaseModel):
    """Body for POST /thesis/{id}/recommendations/{rec_id}/apply.

    action = "accept"  → apply recommended_status lên assumption/catalyst, mark ACCEPTED
    action = "reject"  → mark REJECTED, không thay đổi gì khác
    """

    action: Literal["accept", "reject"] = Field(
        ..., description="'accept' để áp dụng đề xuất, 'reject' để bỏ qua"
    )
    
class ApplyAiReviewRequest(BaseModel):
    """Body cho POST /thesis/{thesis_id}/ai-review/apply (bulk apply).

    Dùng khi user bấm 'Áp dụng gợi ý' trong modal:
    - applied_recommendation_ids: danh sách recommendation mà user CHỌN áp dụng
      (cả assumption lẫn catalyst).
    - verdict / ai_confidence: snapshot verdict tổng thể của AI tại thời điểm này
      (optional, để lưu vào thesis nếu cần).
    """

    applied_recommendation_ids: list[int] = Field(
        default_factory=list,
        description="Danh sách ReviewRecommendation.id được apply (assumption + catalyst)",
    )
    verdict: str | None = Field(
        default=None,
        description="Verdict tổng thể từ AI (BULLISH | BEARISH | NEUTRAL | WATCHLIST)",
    )
    ai_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Mức tin cậy của AI cho verdict (0.0-1.0)",
    )
