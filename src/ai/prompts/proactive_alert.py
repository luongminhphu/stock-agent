"""
Proactive Alert Prompt Pack — ai segment.
Owner: ai segment.

Builds system/user prompts cho ProactiveAlertAgent.
Output schema: ProactiveAlertOutput (Pydantic BaseModel defined in ai.schemas).

Boundary: pure data + string builders. No I/O, no DB, no bus imports.
"""
from __future__ import annotations

from src.ai.prompts._spec import PromptSpec, schema_block, with_persona
from src.ai.schemas import ProactiveAlertOutput

_DOMAIN_RULES = """\
Nhiệm vụ: Phân tích tín hiệu thị trường mới phát hiện và đưa ra khuyến nghị hành động rõ ràng.
Đây là alert thực chiến — không phải bản tin. Nhà đầu tư cần biết LÀM GÌ NGAY BÂY GIỜ.

Nguyên tắc phân tích:
- Verdict phải ngắn, sắc, có thể hành động được ngay — không vòng vo.
- Confidence phản ánh độ chắc chắn thực sự: signal yếu hoặc thiếu context → confidence thấp.
- Risk signals phải cụ thể (giá hỗ trợ, khối lượng, sự kiện sắp tới), không chung chung.
- next_watch_items là mốc/sự kiện cụ thể: giá breakout, ngày BCTC, khối lượng ngưỡng xác nhận.
- Ưu tiên bảo toàn vốn: khi uncertainty cao → WATCH hoặc HOLD, urgency = MONITORING.
- Nếu có thesis AT_RISK hoặc INVALIDATED → ưu tiên SELL/REDUCE hơn BUY, nói thẳng lý do.
- Nếu thesis HEALTHY và signal đủ mạnh → có thể ADD, nhưng phải nêu điều kiện invalidation.

Context thị trường Việt Nam:
- Giờ giao dịch 9:00–15:00 ICT; lưu ý T+2 settlement.
- Thanh khoản HOSE tốt hơn HNX/UPCoM — exit plan quan trọng với mid/small cap.
- Biến động sàn/trần ±7% — có thể mắc kẹt nhiều phiên liên tiếp.
- Room nước ngoài ảnh hưởng định giá một số mã.
- risk_signals là list[object] — mỗi item HAI TRƯỜNG: description (string) và severity (LOW/MEDIUM/HIGH).
  KHÔNG trả về list[string].
""" + schema_block(ProactiveAlertOutput)

SYSTEM_PROMPT = with_persona(_DOMAIN_RULES)

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
    investor_context: str = "",
) -> str:
    """Build user prompt từ SignalDetectedEvent fields.

    Args:
        symbol:           Mã cổ phiếu (e.g. "VCB").
        signal_type:      Loại tín hiệu từ SignalEngine (e.g. "BREAKOUT").
        strength:         Độ mạnh 0.0-1.0 từ SignalEngine.
        confidence:       Độ tin cậy 0.0-1.0 từ SignalEngine.
        source:           Nguồn tín hiệu (e.g. "technical", "news", "combined").
        metadata:         Dict bổ sung từ SignalReport.metadata.
        investor_context: Chuỗi context nhà đầu tư từ ContextBuilder.render_for_agent().
                          Mặc định "" — backward compat, không bắt buộc.

    Returns:
        User prompt string để pass vào AIClient.chat().
    """
    meta_lines = (
        "\n".join(f"  - {k}: {v}" for k, v in metadata.items() if v is not None)
        or "  (không có)"
    )

    context_block = (
        f"\nBối cảnh nhà đầu tư:\n{investor_context}\n" if investor_context else ""
    )

    return f"""\
Tín hiệu mới cần quyết định hành động:
{context_block}
- Mã: **{symbol}**
- Loại tín hiệu: {signal_type}
- Độ mạnh (strength):     {strength:.2f} / 1.00
- Độ tin cậy engine (confidence): {confidence:.2f} / 1.00
- Nguồn: {source}
- Metadata bổ sung:
{meta_lines}

Phân tích tín hiệu và đưa ra khuyến nghị hành động theo JSON schema.
Nếu có bối cảnh nhà đầu tư ở trên, tích hợp vào phân tích:
- thesis AT_RISK hoặc INVALIDATED → ưu tiên SELL/REDUCE, nêu lý do thẳng.
- thesis HEALTHY → có thể cân nhắc ADD nếu signal đủ mạnh, nêu điều kiện invalidation.
Thị trường Việt Nam, phiên 9:00–15:00 ICT, biến động sàn/trần ±7%.
"""
