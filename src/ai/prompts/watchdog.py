"""Prompt pack for Invalidation Trigger Watchdog.

Owner: ai segment.
Used by: WatchdogAgent (ai/agents/watchdog.py).

Responsibility boundary:
  This module owns ONLY the prompt text and user-prompt builder.
  Health scoring logic, API calls, result parsing live in the agent.
  Alert routing and DB writes live in thesis.watchdog_service.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.ai.prompts._spec import PromptSpec, schema_block
from src.ai.schemas import WatchdogOutput


@dataclass
class AssumptionSnapshot:
    """Lightweight snapshot of one assumption for watchdog evaluation."""

    assumption_id: int
    description: str
    current_status: str  # valid | invalid | uncertain | pending
    last_note: str = ""


@dataclass
class WatchdogContext:
    """All context needed to evaluate thesis health."""

    thesis_id: int
    ticker: str
    thesis_title: str
    thesis_summary: str
    assumptions: list[AssumptionSnapshot] = field(default_factory=list)
    current_price: float | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    macro_context: str = "N/A"  # injected from market segment
    recent_news: str = "N/A"  # injected from market.news_service
    days_since_last_review: int = 0
    # Wave D1: dòng market.TickerContext.format_for_prompt() (MA20/50, RSI14, ATR14,
    # Vol/TB20, 52w, trend, chất lượng dữ liệu). Rỗng → bỏ section.
    ticker_context: str = ""


SYSTEM_PROMPT = """\
Bạn là chuyên gia giám sát luận điểm đầu tư cho thị trường chứng khoán Việt Nam.

Nhiệm vụ: Đánh giá sức khoẻ tổng thể của một thesis đầu tư dựa trên trạng thái hiện tại của từng assumption, giá cổ phiếu, bối cảnh kỹ thuật và bối cảnh vĩ mô (nếu có).

Quy tắc bắt buộc:
1. Phải đánh giá TỪNG assumption riêng lẻ — không được gộp chung.
2. `threat_level` cho mỗi assumption: "none" | "low" | "medium" | "high".
3. `threat_reason` phải gắn với dữ liệu cụ thể — không được nói chung chung.
4. `recommended_action`: "HOLD" | "REVIEW_SOON" | "REVIEW_URGENT" | "CONSIDER_EXIT".
5. `overall_health`: "HEALTHY" khi health_score ≥70, "WARNING" 40–69, "CRITICAL" <40.
6. Trả về JSON hợp lệ, không có markdown hoặc giải thích ngoài JSON.
7. `confidence` phải là string: "HIGH" | "MEDIUM" | "LOW".
8. Chỉ dùng số liệu kỹ thuật được cung cấp; nếu data = stale/fallback thì không suy diễn thêm.

""" + schema_block(WatchdogOutput)

SPEC = PromptSpec(
    agent_name="WatchdogAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=WatchdogOutput,
    max_tokens=800,
)


def build_user_prompt(ctx: WatchdogContext, investor_profile: str = "") -> str:
    """Build the user-turn prompt from a WatchdogContext.

    Args:
        ctx:              WatchdogContext with thesis + market data.
        investor_profile: Optional rendered InvestorContext from ContextBuilder.
                          Appended after the main block when provided.
    """
    price_section = "Không có dữ liệu giá."
    if ctx.current_price and ctx.entry_price:
        pnl = (ctx.current_price - ctx.entry_price) / ctx.entry_price * 100
        stop_dist = (
            (ctx.current_price - ctx.stop_loss) / ctx.current_price * 100 if ctx.stop_loss else None
        )
        target_dist = (
            (ctx.target_price - ctx.current_price) / ctx.current_price * 100
            if ctx.target_price
            else None
        )
        price_section = (
            f"Giá hiện tại: {ctx.current_price:,.0f} VND | "
            f"Entry: {ctx.entry_price:,.0f} | "
            f"P&L: {pnl:+.1f}%"
        )
        if stop_dist is not None:
            price_section += f" | Cách stop-loss: {stop_dist:.1f}%"
        if target_dist is not None:
            price_section += f" | Cách target: {target_dist:+.1f}%"

    assumptions_text = (
        "\n".join(
            f"  [{i + 1}] ID={a.assumption_id} | {a.current_status.upper()} | "
            f"{a.description}" + (f" [Note: {a.last_note}]" if a.last_note else "")
            for i, a in enumerate(ctx.assumptions)
        )
        or "  (Không có assumption nào)"
    )

    stale_note = (
        f"\n⚠️ Thesis chưa được review trong {ctx.days_since_last_review} ngày."
        if ctx.days_since_last_review > 14
        else ""
    )

    # Wave D1: bối cảnh kỹ thuật từ market.TickerContext — số thật, không suy diễn.
    technical_section = (
        f"\nBối cảnh kỹ thuật:\n{ctx.ticker_context}\n"
        "(Nếu data = stale/fallback: chỉ báo có thể thiếu hoặc cũ — không suy diễn thêm.)\n"
        if ctx.ticker_context
        else ""
    )

    # Chỉ render vĩ mô / tin tức khi caller thực sự inject (mặc định "N/A" → bỏ).
    context_lines = [
        f"Bối cảnh vĩ mô: {ctx.macro_context}" if ctx.macro_context != "N/A" else "",
        f"Tin tức gần nhất: {ctx.recent_news}" if ctx.recent_news != "N/A" else "",
    ]
    context_section = "\n".join(line for line in context_lines if line)

    prompt = f"""Mã: {ctx.ticker}
Thesis: {ctx.thesis_title}
Tóm tắt: {ctx.thesis_summary or "N/A"}

Giá và vị thế:
{price_section}
{technical_section}
Assumptions ({len(ctx.assumptions)}):
{assumptions_text}
"""
    if context_section:
        prompt += f"\n{context_section}\n"
    prompt += f"{stale_note}\nĐánh giá sức khoẻ thesis và trả về JSON theo schema ở trên."

    if investor_profile:
        prompt += f"\n\n{investor_profile}"

    return prompt
