"""Schema for NextActionSuggester output.

Owner: ai segment.
Caller: BriefingService (post-brief synthesis), bot scheduler, API layer.

Design note:
  NextActionPlan is the cross-agent synthesis layer. It consumes outputs from
  multiple agents (BriefingAgent, ThesisJudgeAgent, ThesisInvalidationDetector,
  WatchdogAgent, SignalEngineAgent) and produces an ordered, investor-facing
  action list — one SuggestedAction per ticker/thesis that needs attention.

  Distinct from BriefOutput.ActionQueue:
    - ActionQueue: macro-level priority labels from BriefingAgent alone.
    - NextActionPlan: per-ticker specific steps, cross-referenced from all
      available signal outputs, ordered by urgency score.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class ActionScope(StrEnum):
    """Which domain/concern this action targets.

    THESIS_REVIEW     — thesis cần được review lại.
    THESIS_INVALIDATE — thesis có dấu hiệu cần đóng/exit.
    POSITION_MANAGE   — cần điều chỉnh position (reduce/add).
    WATCHLIST_MONITOR — theo dõi sát, chưa cần hành động.
    CATALYST_TRACK    — catalyst sắp xảy ra, cần chuẩn bị.
    SIGNAL_RESPOND    — tín hiệu kỹ thuật/dòng tiền cần phản ứng.
    PORTFOLIO_REBALANCE — cân bằng lại tỷ trọng danh mục.
    """

    THESIS_REVIEW = "THESIS_REVIEW"
    THESIS_INVALIDATE = "THESIS_INVALIDATE"
    POSITION_MANAGE = "POSITION_MANAGE"
    WATCHLIST_MONITOR = "WATCHLIST_MONITOR"
    CATALYST_TRACK = "CATALYST_TRACK"
    SIGNAL_RESPOND = "SIGNAL_RESPOND"
    PORTFOLIO_REBALANCE = "PORTFOLIO_REBALANCE"


class SuggestedAction(BaseModel):
    """A single actionable item for a specific ticker or portfolio-level concern.

    Downstream consumers:
      - bot: format as Discord message with urgency emoji + step.
      - readmodel: render in dashboard priority queue, ordered by urgency_score.
      - api: return in /next-actions endpoint.
    """

    ticker: str = Field(
        description="Mã cổ phiếu. 'PORTFOLIO' for portfolio-level actions."
    )
    thesis_id: str | None = Field(
        default=None,
        description="Thesis ID nếu action liên quan trực tiếp đến một thesis."
    )
    scope: ActionScope
    urgency: Literal["critical", "high", "medium", "low"] = Field(
        description="critical: cần hành động hôm nay. high: trong 1-2 ngày. "
                    "medium: trong tuần. low: theo dõi."
    )
    urgency_score: float = Field(
        ge=0.0, le=1.0,
        description="Numeric urgency for sorting. 1.0 = most urgent."
    )
    title: str = Field(
        description="Tiêu đề ngắn (< 10 từ) cho bot alert và dashboard card."
    )
    step: str = Field(
        description="Bước hành động cụ thể, 1-2 câu. Viết cho nhà đầu tư, "
                    "không phải log kỹ thuật."
    )
    rationale: str = Field(
        description="Lý do 1-2 câu: tại sao action này quan trọng tại thời điểm này."
    )
    source_signals: list[str] = Field(
        default_factory=list,
        description="Các agent/signal đã trigger action này, e.g. "
                    "['ThesisJudge:WEAKENING', 'Watchdog:BEARISH', 'stop_loss_breach']."
    )
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("urgency_score", mode="before")
    @classmethod
    def coerce_urgency_score(cls, v: object) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, f))

    @field_validator("source_signals", mode="before")
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


class NextActionPlan(BaseModel):
    """Ordered list of suggested actions across all tickers and portfolio.

    actions is sorted by urgency_score DESC — highest urgency first.
    Caller MUST NOT re-sort; order is the product output.

    summary: 1-2 câu tổng hợp cho đầu bot message / briefing header.
    generated_at: ISO 8601 timestamp, stamped by agent.
    """

    actions: list[SuggestedAction] = Field(
        default_factory=list,
        description="Ordered list of actions, urgency_score DESC."
    )
    summary: str = Field(
        default="",
        description="1-2 câu tổng hợp: hôm nay cần chú ý gì nhất."
    )
    total_critical: int = Field(
        default=0,
        description="Số action có urgency=critical, for badge/notification."
    )
    generated_at: str = Field(
        default="",
        description="ISO 8601 timestamp — stamped by agent."
    )

    @field_validator("actions", mode="before")
    @classmethod
    def ensure_actions_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
