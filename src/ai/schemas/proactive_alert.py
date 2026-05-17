"""
Schemas for ProactiveAlertAgent.

Owner: ai segment.
Consumed by: ProactiveAlertAgent -> RecommendationReadyEvent -> bot/api.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class RiskSignal(BaseModel):
    """Một risk signal cụ thể liên quan đến mã chứng khoán."""

    description: str = Field(description="Mô tả rủi ro cụ thể, tiếng Việt")
    severity: Literal["LOW", "MEDIUM", "HIGH"]


class ProactiveAlertOutput(BaseModel):
    """Structured output từ AIClient cho mỗi SignalDetectedEvent.

    Owner: ai segment.
    Consumed by: ProactiveAlertAgent -> RecommendationReadyEvent -> bot/api.
    Phải map toàn bộ các field của RecommendationReadyEvent.
    """

    action: Literal["BUY", "SELL", "REDUCE", "HOLD", "WATCH"] = Field(
        description="Khả năng hành động được khuyến nghị"
    )
    urgency: Literal["NOW", "TODAY", "THIS_WEEK", "MONITORING"] = Field(
        description="Mức độ khẩn cấp của khả năng hành động"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Độ tin cậy của AI với phân tích này (0.0 - 1.0)",
    )
    verdict: str = Field(
        description="1-2 câu verdict ngắn gọn, tiếng Việt, có thể hành động được ngay"
    )
    risk_signals: list[RiskSignal] = Field(
        default_factory=list,
        description="Danh sách rủi ro cụ thể cần lưu ý (tối đa 4)",
    )
    next_watch_items: list[str] = Field(
        default_factory=list,
        description="Những mốc/sự kiện cụ thể cần theo dõi tiếp theo (tối đa 3)",
    )
    reasoning: str = Field(
        description="Lý do chi tiết hơn cho verdict, tối đa 150 từ"
    )
