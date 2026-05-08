"""Prompt pack for Signal Credibility Scorer.

Owner: ai segment.
Used by: SignalCredibilityAgent (ai/agents/signal_credibility.py).

Responsibility boundary:
  This module owns ONLY the prompt text and user-prompt builder.
  Scoring logic, API calls, and result parsing live in the agent.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ai.prompts._spec import PromptSpec, schema_block
from src.ai.schemas import SignalCredibilityOutput


@dataclass
class SignalCredibilityContext:
    """All context needed to evaluate a signal's credibility."""

    ticker: str
    signal_type: str          # e.g. "breakout", "strong_move", "alert_triggered"
    current_price: float
    change_pct: float
    volume_ratio: float       # current volume / 20-day average (1.0 = average)
    price_5d_trend: float     # 5-day price change % (positive = uptrend)
    recent_news: str          # short summary of latest news, or "N/A"
    has_upcoming_earnings: bool
    alert_note: str = ""      # alert label/condition if alert_triggered, else ""
    historical_hit_rate: float | None = None  # % of same signal type that succeeded (0-1), or None if unknown


SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích kỹ thuật và định lượng cho thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM).

Nhiệm vụ: Đánh giá độ tin cậy của một tín hiệu giao dịch dựa trên các yếu tố kỹ thuật, khối lượng, xu hướng, và bối cảnh.

Quy tắc bắt buộc:
1. Không được chỉ nói "tín hiệu tốt" hay "cần theo dõi" mà không có bằng chứng cụ thể.
2. `failure_risks` PHẢI là list[string] — mỗi item là một chuỗi văn bản, KHÔNG phải object.
   Liệt kê ÍT NHẤT 2 lý do cụ thể vì sao tín hiệu này có thể là false positive.
3. `supporting_factors` chỉ được ghi nếu có dữ liệu hỗ trợ thực sự — không được bịa.
   Đây cũng là list[string], không phải list[object].
4. `score` phải nhất quán với `verdict`: STRONG ≥ 70, MODERATE 45–69, WEAK 20–44, NOISE < 20.
5. `confidence` phải là string: "HIGH" | "MEDIUM" | "LOW".
6. Trả về JSON hợp lệ, không có markdown hoặc giải thích ngoài JSON.

""" + schema_block(SignalCredibilityOutput)

SPEC = PromptSpec(
    agent_name="SignalCredibilityAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=SignalCredibilityOutput,
)


def build_user_prompt(ctx: SignalCredibilityContext) -> str:
    """Build the user-turn prompt from a SignalCredibilityContext."""
    hit_rate_line = (
        f"- Lịch sử tín hiệu cùng loại: {ctx.historical_hit_rate:.0%} thành công"
        if ctx.historical_hit_rate is not None
        else "- Lịch sử tín hiệu cùng loại: chưa có dữ liệu"
    )
    alert_line = (
        f"- Điều kiện alert đã kích hoạt: {ctx.alert_note}"
        if ctx.alert_note
        else ""
    )
    earnings_line = (
        "- ⚠️ Sắp có kết quả kinh doanh — tín hiệu kỹ thuật dễ bị nhiễu"
        if ctx.has_upcoming_earnings
        else "- Không có sự kiện earnings sắp tới"
    )
    volume_note = (
        f"tăng mạnh ({ctx.volume_ratio:.1f}× TB 20 phiên)"
        if ctx.volume_ratio >= 1.5
        else f"bình thường ({ctx.volume_ratio:.1f}× TB 20 phiên)"
        if ctx.volume_ratio >= 0.8
        else f"thấp bất thường ({ctx.volume_ratio:.1f}× TB 20 phiên)"
    )
    trend_note = (
        f"tăng {ctx.price_5d_trend:+.1f}% trong 5 phiên"
        if ctx.price_5d_trend > 1
        else f"giảm {ctx.price_5d_trend:+.1f}% trong 5 phiên"
        if ctx.price_5d_trend < -1
        else "đi ngang trong 5 phiên"
    )

    lines = [
        f"Mã: {ctx.ticker}",
        f"Loại tín hiệu: {ctx.signal_type}",
        f"Giá hiện tại: {ctx.current_price:,.0f} VND  |  Thay đổi phiên: {ctx.change_pct:+.2f}%",
        f"Khối lượng: {volume_note}",
        f"Xu hướng ngắn hạn: {trend_note}",
        earnings_line,
        hit_rate_line,
    ]
    if alert_line:
        lines.append(alert_line)
    lines += [
        f"Tin tức gần nhất: {ctx.recent_news}",
        "",
        "Đánh giá độ tin cậy của tín hiệu này và trả về JSON theo schema ở trên.",
    ]
    return "\n".join(line for line in lines if line is not None)
