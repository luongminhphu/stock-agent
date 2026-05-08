"""
Proactive Alert Prompt Pack — ai segment.
Owner: ai segment.

Builds system/user prompts cho ProactiveAlertAgent.
Output schema: ProactiveAlertOutput (Pydantic BaseModel).

Boundary: pure data + string builders. No I/O, no DB, no bus imports.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.ai.prompts._spec import PromptSpec, schema_block


# ── Output Schema ──────────────────────────────────────────────────────────────


class RiskSignal(BaseModel):
    """Một risk signal cụ thể liên quan đến mã chứng khoán."""

    description: str = Field(description="Mô tả rủi ro cụ thể, tiếng Việt")
    severity: Literal["LOW", "MEDIUM", "HIGH"]


class ProactiveAlertOutput(BaseModel):
    """Structured output từ AIClient cho mỗi SignalDetectedEvent.

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
        description="Độ tin cậy của AI với phân tích này (0.0 – 1.0)",
    )
    verdict: str = Field(
        description="1–2 câu verdict ngắn gọn, tiếng Việt, có thể hành động được ngay"
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


# ── Prompt Builders ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích chứng khoán Việt Nam (HOSE/HNX/UPCoM), chuyên về
phân tích tín hiệu kỹ thuật và đưa ra khả năng hành động có cấu trúc cho nhà đầu tư cá nhân.

Nguyên tắc phân tích:
- Verdict phải ngắn, sắc, có thể hành động được ngay — không vòng vo.
- Confidence phản ánh độ chắc chắn thực sự: signal yếu hoặc thiếu context → confidence thấp.
- Risk signals phải cụ thể (giá hỗ trợ, khối lượng, sự kiện sắp tới), không chung chung.
- next_watch_items là mốc/sự kiện cụ thể: giá breakout, ngày BCTC, khối lượng ngưỡng xác nhận.
- Ưu tiên bảo toàn vốn: khi uncertainty cao → WATCH hoặc HOLD, urgency = MONITORING.
- Đối với thị trường Việt Nam: lưu ý giờ hạn đặt lệnh (9:00–15:00 ICT), room nước ngoài,
  tính thanh khoản (HOSE tốt hơn HNX/UPCoM), và biến động sàn/trần (±7%).
- Tất cả các field text trả lời bằng tiếng Việt.
- risk_signals là list[object] — mỗi item CÓ HAI TRƯỜNG: description (string) và severity (LOW/MEDIUM/HIGH).
  KHÔNG trả về list[string] — phải là list[{\"description\": ..., \"severity\": ...}].
""" + schema_block(ProactiveAlertOutput)

SPEC = PromptSpec(
    agent_name="ProactiveAlertAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=ProactiveAlertOutput,
)


def build_user_prompt(
    symbol: str,
    signal_type: str,
    strength: float,
    confidence: float,
    source: str,
    metadata: dict,
) -> str:
    """Build user prompt từ SignalDetectedEvent fields.

    Args:
        symbol:      Mã cổ phiếu (e.g. "VCB").
        signal_type: Loại tín hiệu từ SignalEngine (e.g. "BREAKOUT").
        strength:    Độ mạnh 0.0–1.0 từ SignalEngine.
        confidence:  Độ tin cậy 0.0–1.0 từ SignalEngine.
        source:      Nguồn tín hiệu (e.g. "technical", "news", "combined").
        metadata:    Dict bổ sung từ SignalReport.metadata.

    Returns:
        User prompt string để pass vào AIClient.chat().
    """
    meta_lines = "\n".join(
        f"  - {k}: {v}" for k, v in metadata.items() if v is not None
    ) or "  (không có)"

    return f"""\
Tín hiệu mới phát hiện cần phân tích:

- Mã: **{symbol}**
- Loại tín hiệu: {signal_type}
- Độ mạnh (strength):     {strength:.2f} / 1.00
- Độ tin cậy engine (confidence): {confidence:.2f} / 1.00
- Nguồn: {source}
- Metadata bổ sung:
{meta_lines}

Hãy phân tích và đưa ra khả năng hành động theo JSON schema ở trên.
Nhớ: thị trường Việt Nam, phiên giao dịch 9:00–15:00 ICT, biến động sàn/trần ±7%.
"""
