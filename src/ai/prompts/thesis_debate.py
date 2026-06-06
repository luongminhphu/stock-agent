"""Prompt pack cho Thesis Debate Mode.

Owner: ai segment.
Caller: ThesisDebateAgent.run() only.

Design notes:
- temperature=0.4: higher than judge (0.3) to surface diverse attack angles.
- Persona: sói già Phố Wall đóng vai devil's advocate — đủ hung hăng để hữu ích.
- Vietnamese market context is explicitly injected (HOSE/HNX room, margin cycles, etc.).
- debate_focus param allows user to narrow the debate to a specific decision.
"""
from __future__ import annotations

from typing import Any

from src.ai.client import AISpec
from src.ai.prompts._spec import with_persona
from src.ai.schemas.thesis_debate import DebateOutput

_DOMAIN_RULES = """\
Hôm nay bạn đóng vai devil's advocate — fund manager kỳ cựu phản biện thẳng vào thesis.

Nhiệm vụ: Phân tích investment thesis và tạo ra những phản biện sắc bén, có cơ sở.
Mục tiêu không phải phá hủy thesis — mà buộc nhà đầu tư phải suy nghĩ rõ ràng hơn.

Nguyên tắc bắt buộc:
1. Không xác nhận lại những gì investor đã biết — tìm góc nhìn họ chưa thấy.
2. Tấn công assumption YẾU NHẤT, không phải mạnh nhất — đó là điểm dễ bị invalidate nhất.
3. Mỗi challenge phải có evidence cụ thể: số liệu, precedent lịch sử, hoặc logic nhân quả.
4. Nếu thesis thực sự mạnh, nói thẳng — đừng phản biện giả tạo chỉ để có output.
5. Mỗi challenge phải kèm rebuttal_hint — nhà đầu tư phải có cơ hội tự bảo vệ.
6. confidence_adjustment phải phản ánh đúng chất lượng challenges — không inflate.

Context thị trường Việt Nam (bắt buộc tích hợp khi phân tích):
- Thanh khoản HOSE/HNX thường thấp → slippage risk khi exit position lớn.
- Room ngoại có thể bị fill → ảnh hưởng định giá premium/discount.
- Biên độ ±7% (HOSE) có thể trap position trong downtrend kéo dài.
- Chu kỳ margin call thường khuếch đại volatility — đừng đánh giá thấp.
- Sở hữu nhà nước chi phối → rủi ro chính sách và quản trị thường bị underpriced.
- KQKD quarterly là catalyst chính — timing vào/ra quan trọng hơn đúng/sai thesis.
- Chu kỳ bất động sản liên kết chặt với banking và vật liệu xây dựng.

Trả lời bằng tiếng Việt trừ khi thesis viết hoàn toàn bằng tiếng Anh.
"""

_SYSTEM = with_persona(_DOMAIN_RULES)

SPEC = AISpec(
    system_prompt=_SYSTEM,
    output_schema=DebateOutput,
    temperature=0.4,
    max_tokens=1800,
)


def build_user_prompt(
    *,
    thesis_id: str | int,
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions: list[dict[str, Any]],
    catalysts: list[dict[str, Any]],
    invalidation_conditions: list[str],
    price_context: dict[str, Any] | None = None,
    recent_news: list[str] | None = None,
    days_since_written: int | None = None,
    conviction_current: float | None = None,
    debate_focus: str | None = None,
    investor_context: str = "",
) -> str:
    """Build user prompt for ThesisDebateAgent.

    Args:
        thesis_id:               Thesis ID for traceability.
        ticker:                  Mã cổ phiếu.
        thesis_title:            Tiêu đề thesis.
        thesis_summary:          Luận điểm đầy đủ.
        assumptions:             [{"id", "description", "status"}]
        catalysts:               [{"id", "description", "status"}]
        invalidation_conditions: Điều kiện investor đã tự đặt.
        price_context:           {"price", "change_1w", "change_1m", "volume_trend"}
        recent_news:             Max 5 tin tức gần đây.
        days_since_written:      Số ngày từ khi viết thesis.
        conviction_current:      Conviction hiện tại (0.0-1.0).
        debate_focus:            "entry" | "exit" | "sizing" | None (general)
    """
    lines: list[str] = []

    # Inject investor memory context first so the model has behavioural framing
    # before reading the thesis — same pattern as thesis_judge.
    if investor_context:
        lines += [
            "## Investor Context",
            investor_context,
            "",
        ]

    lines += [
        "## Thesis Debate Request",
        f"Ticker: **{ticker}** | Thesis ID: {thesis_id}",
        f"Tiêu đề: {thesis_title}",
        "",
        "### Luận điểm gốc",
        thesis_summary or "(không có tóm tắt)",
        "",
    ]

    if assumptions:
        lines += ["### Assumptions đang giữ"]
        for i, a in enumerate(assumptions, 1):
            status = a.get("status", "active")
            desc = a.get("description", "")
            lines.append(f"{i}. [{status.upper()}] {desc}")
        lines.append("")

    if catalysts:
        lines += ["### Catalysts đang chờ"]
        for c in catalysts:
            desc = c.get("description", "")
            status = c.get("status", "")
            lines.append(f"- {desc} ({status})")
        lines.append("")

    if invalidation_conditions:
        lines += ["### Điều kiện invalidation investor đã tự đặt"]
        for cond in invalidation_conditions:
            lines.append(f"- {cond}")
        lines.append("")

    if price_context:
        lines += ["### Dữ liệu giá hiện tại"]
        for key, label in [
            ("price", "Giá"),
            ("change_1w", "% thay đổi 1 tuần"),
            ("change_1m", "% thay đổi 1 tháng"),
            ("volume_trend", "Volume trend"),
        ]:
            val = price_context.get(key)
            if val is not None:
                lines.append(f"- {label}: {val}")
        lines.append("")

    if recent_news:
        lines += ["### Tin tức gần đây (tối đa 5)"]
        for n in recent_news[:5]:
            lines.append(f"- {n}")
        lines.append("")

    meta: list[str] = []
    if days_since_written is not None:
        meta.append(f"Thesis viết cách đây **{days_since_written} ngày**")
    if conviction_current is not None:
        meta.append(f"Conviction hiện tại: **{conviction_current:.0%}**")
    if meta:
        lines += ["### Metadata", *meta, ""]

    if debate_focus:
        _focus_map = {
            "entry": (
                "**Debate Focus: ENTRY**\n"
                "Tập trung phản biện quyết định MUA VÀO ngay lúc này. "
                "Timing có hợp lý không? Có catalyst gần không? Entry price có margin of safety không?"
            ),
            "exit": (
                "**Debate Focus: EXIT**\n"
                "Tập trung phản biện quyết định BÁN / CẮT LỖ ngay lúc này. "
                "Thesis còn hợp lệ không? Có đang bán vì sợ thay vì lý do cơ bản không?"
            ),
            "sizing": (
                "**Debate Focus: SIZING**\n"
                "Tập trung phản biện về việc TĂNG/GIẢM tỷ trọng hiện tại. "
                "Concentration risk có quá cao không? Risk/reward còn asymmetric không?"
            ),
        }
        lines += [
            "### Debate Focus",
            _focus_map.get(debate_focus, f"Focus: {debate_focus}"),
            "",
        ]

    lines += [
        "---",
        "Đóng vai devil's advocate và debate thesis này.",
        "Tấn công assumption yếu nhất — không phải mạnh nhất.",
        "Mỗi challenge phải có evidence cụ thể, không chung chung.",
        "Nếu thesis thực sự mạnh, nói thẳng — đừng phản biện giả tạo.",
        "Output theo schema DebateOutput.",
    ]

    return "\n".join(lines)
