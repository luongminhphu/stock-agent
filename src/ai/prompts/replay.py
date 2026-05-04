"""AI prompt pack for Decision Replay & Learning Loop.

Owner: ai segment.
Used by: ReplayAgent.

This prompt analyzes one past investor decision against its frozen context
and realized outcome after a review horizon (e.g. 30/90 days).
The prompt does NOT decide trade execution. It only explains what was right,
what was wrong, and what pattern may be recurring in the investor's behavior.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReplayContext:
    decision_id: int
    thesis_id: int
    ticker: str
    decision_type: str  # BUY | SELL | HOLD | ADD | REDUCE
    decision_at: str
    rationale: str
    price_at_decision: float | None
    thesis_score_at_decision: float | None
    thesis_health_score_at_decision: int | None
    active_signal: str | None
    brief_summary: str | None
    outcome_price: float | None
    outcome_pnl_pct: float | None
    outcome_horizon_days: int
    outcome_verdict_hint: str | None = None


SYSTEM_PROMPT = """\
Bạn là AI review coach cho nhà đầu tư chứng khoán Việt Nam.

Nhiệm vụ: phân tích MỘT quyết định đầu tư đã xảy ra trong quá khứ, dựa trên:
- ngữ cảnh tại thời điểm ra quyết định,
- thesis score / health score tại thời điểm đó,
- signal đang active,
- brief context,
- kết quả thực tế sau 30 hoặc 90 ngày.

Mục tiêu không phải phán xét chung chung, mà là rút ra bài học cụ thể giúp nhà đầu tư ra quyết định tốt hơn lần sau.

Quy tắc bắt buộc:
1. Chỉ phân tích quyết định đã cung cấp, không suy diễn sang mã khác.
2. `outcome_verdict` phải là một trong: CORRECT | INCORRECT | MIXED.
3. `what_went_right` và `what_went_wrong` phải là các bullet ngắn, rất cụ thể.
4. `pattern_detected` chỉ điền khi thật sự có dấu hiệu hành vi lặp lại từ context; nếu không có thì để null.
5. `suggested_adjustment` phải actionable, ngắn gọn.
6. Trả về JSON hợp lệ, không có markdown.

JSON schema:
{
  "decision_id": <int>,
  "ticker": "...",
  "decision_type": "BUY" | "SELL" | "HOLD" | "ADD" | "REDUCE",
  "outcome_verdict": "CORRECT" | "INCORRECT" | "MIXED",
  "what_went_right": ["..."],
  "what_went_wrong": ["..."],
  "key_lesson": "...",
  "pattern_detected": "..." | null,
  "suggested_adjustment": "..." | null,
  "confidence": "HIGH" | "MEDIUM" | "LOW"
}
"""


def build_user_prompt(ctx: ReplayContext) -> str:
    decision_price = (
        f"{ctx.price_at_decision:,.0f} VND" if ctx.price_at_decision is not None else "N/A"
    )
    thesis_score = (
        f"{ctx.thesis_score_at_decision:.1f}" if ctx.thesis_score_at_decision is not None else "N/A"
    )
    health_score = (
        str(ctx.thesis_health_score_at_decision)
        if ctx.thesis_health_score_at_decision is not None
        else "N/A"
    )
    outcome_price = (
        f"{ctx.outcome_price:,.0f} VND" if ctx.outcome_price is not None else "N/A"
    )
    outcome_pnl = (
        f"{ctx.outcome_pnl_pct:+.1f}%" if ctx.outcome_pnl_pct is not None else "N/A"
    )

    return f"""Decision ID: {ctx.decision_id}
Thesis ID: {ctx.thesis_id}
Ticker: {ctx.ticker}
Decision type: {ctx.decision_type}
Decision at: {ctx.decision_at}

Context tại thời điểm quyết định:
- Price at decision: {decision_price}
- Thesis score: {thesis_score}
- Thesis health score: {health_score}
- Active signal: {ctx.active_signal or 'N/A'}
- Brief summary: {ctx.brief_summary or 'N/A'}
- Investor rationale: {ctx.rationale or 'N/A'}

Kết quả sau {ctx.outcome_horizon_days} ngày:
- Outcome price: {outcome_price}
- Outcome PnL: {outcome_pnl}
- Outcome verdict hint: {ctx.outcome_verdict_hint or 'N/A'}

Hãy phân tích quyết định này và trả về JSON theo schema đã định."""
