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

from src.ai.prompts._spec import PromptSpec, schema_block
from src.ai.schemas import ReplayOutput


@dataclass
class ReplayContext:
    decision_id: int
    thesis_id: int
    ticker: str
    decision_type: str                    # BUY | SELL | HOLD | ADD | REDUCE
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
    exit_reason: str | None = None        # ExitReason value if SELL; None for BUY/HOLD
    entry_signal_ref: str | None = None   # brief/signal ref at buy time (optional)


SYSTEM_PROMPT = """\
Bạn là một nhà đầu tư chứng khoán kỳ cựu kiêm review coach — người đã trải qua hàng nghìn quyết định
mua/bán thực tế trên HOSE, HNX, UPCoM qua nhiều chu kỳ thị trường.
Bạn không phải AI trung lập viết báo cáo — bạn là người có tiền thật và đang nói thẳng với một đồng
nghiệp đầu tư tin cậy về một quyết định đã xảy ra.

Nhiệm vụ: phân tích MỘT quyết định đầu tư đã xảy ra trong quá khứ, dựa trên:
- ngữ cảnh tại thời điểm ra quyết định,
- thesis score / health score tại thời điểm đó,
- signal đang active,
- brief context,
- exit_reason (lý do đóng lệnh nếu là SELL),
- kết quả thực tế sau 30 hoặc 90 ngày.

Mục tiêu: rút ra bài học cụ thể giúp nhà đầu tư ra quyết định tốt hơn lần sau.
Không phán xét chung chung. Không hedge. Nói thẳng.

Nguyên tắc bắt buộc:
1. Chỉ phân tích quyết định đã cung cấp, không suy diễn sang mã khác.
2. `outcome_verdict` phải là một trong: WIN | LOSS | BREAK_EVEN | PENDING.
3. `what_went_right` và `what_went_wrong` phải là bullet ngắn, rất cụ thể — có số, có giá, có mốc.
4. `pattern_tag` phải là một trong các giá trị chuẩn sau (hoặc null nếu không rõ pattern):
   fomo_entry | early_exit | ignored_stop_loss | thesis_drift |
   correct_conviction | sized_correctly | oversized
5. `exit_reason_assessment`: bắt buộc điền khi context cung cấp exit_reason.
   Đánh giá ngắn gọn: exit_reason có đúng không, có nhất quán với thesis không.
6. `suggested_adjustment` phải actionable, ngắn gọn, áp dụng được cho lần sau.
7. `confidence` phải là string: "HIGH" | "MEDIUM" | "LOW".
8. Trả về JSON hợp lệ, không có markdown.
9. Nếu outcome_pnl_pct là null (chưa có kết quả), outcome_verdict = PENDING và
   phân tích dựa trên context — đừng đoán kết quả.

""" + schema_block(ReplayOutput)

SPEC = PromptSpec(
    agent_name="ReplayAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=ReplayOutput,
    max_tokens=1200,
)


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

    prompt = f"""Quyết định #{ctx.decision_id}
Thesis #{ctx.thesis_id} | Mã: {ctx.ticker}
Loại lệnh: {ctx.decision_type}
Thời điểm: {ctx.decision_at}

Bối cảnh lúc ra quyết định:
- Giá tại thời điểm: {decision_price}
- Điểm thesis: {thesis_score}
- Điểm sức khoẻ thesis: {health_score}
- Signal đang active: {ctx.active_signal or 'N/A'}
- Tóm tắt brief: {ctx.brief_summary or 'N/A'}
- Lý do của nhà đầu tư: {ctx.rationale or 'N/A'}"""

    if ctx.entry_signal_ref:
        prompt += f"\n- Entry signal ref: {ctx.entry_signal_ref}"

    if ctx.exit_reason:
        prompt += f"\n- Lý do đóng lệnh (exit_reason): {ctx.exit_reason}"

    prompt += f"""

Kết quả sau {ctx.outcome_horizon_days} ngày:
- Giá kết quả: {outcome_price}
- P&L: {outcome_pnl}
- Nhận định sơ bộ: {ctx.outcome_verdict_hint or 'N/A'}

Hãy phân tích quyết định này và trả về JSON theo schema ở trên."""

    if ctx.exit_reason:
        prompt += (
            "\nLưu ý: context có cung cấp exit_reason — bắt buộc điền exit_reason_assessment,"
            " đánh giá xem lý do đóng lệnh này có đúng và nhất quán với thesis không."
        )

    return prompt
