"""Prompt pack for AgendaBuilder — Daily Investor Agenda.

Owner: ai segment.
Contract: AgendaContext IN → DailyAgendaResult OUT (JSON).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------

class PendingDecisionItem(BaseModel):
    decision_id: int
    ticker: str
    decision_type: str          # BUY | SELL | HOLD | ADD | REDUCE
    decision_at: str            # ISO date string
    horizon_days: int
    deadline: str               # ISO date string — ngày horizon hết hạn
    days_until_deadline: int    # âm = đã quá hạn
    pnl_pct: float | None       # None nếu chưa evaluate_outcome
    rationale_summary: str | None


class ActiveThesisItem(BaseModel):
    thesis_id: int
    ticker: str
    thesis_title: str
    health_score: int | None    # 0-100
    days_active: int
    next_assumption_check: str | None  # ISO date — ngày assumption sắp được test
    has_pending_decision: bool
    last_reviewed_days_ago: int | None


class MemorySignalItem(BaseModel):
    ticker: str
    pattern_summary: str        # 1 câu từ SemanticPattern.description
    confidence: float


class AgendaContext(BaseModel):
    today: str                  # ISO date
    user_id: str
    pending_decisions: list[PendingDecisionItem]
    active_theses: list[ActiveThesisItem]
    memory_signals: list[MemorySignalItem]
    unreviewed_lessons_count: int   # decisions có key_lesson nhưng user chưa xem


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

class AgendaItem(BaseModel):
    priority: Literal["DECIDE", "WATCH", "DEFER"]
    ticker: str
    urgency_source: Literal[
        "decision_horizon",     # horizon sắp/đã hết hạn
        "assumption_test",      # assumption sắp được kiểm chứng
        "thesis_stale",         # thesis lâu không review
        "memory_signal",        # memory pattern active
        "lesson_pending",       # lesson chưa đọc
    ]
    reason: str                 # 1 câu ngắn, tiếng Việt
    action_hint: str            # hành động cụ thể cần làm
    deadline: str | None        # ISO date nếu có


class DailyAgendaResult(BaseModel):
    decide: list[AgendaItem] = Field(default_factory=list)
    watch: list[AgendaItem] = Field(default_factory=list)
    defer: list[AgendaItem] = Field(default_factory=list)
    opening_line: str           # 1 câu AI tóm tắt ngày
    total_action_items: int


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Bạn là AI advisor của một nhà đầu tư chứng khoán Việt Nam.
Nhiệm vụ: phân tích danh sách thesis, decision horizon, và memory signal
để tạo ra agenda ưu tiên cho ngày hôm nay.

Quy tắc phân loại:
- DECIDE: cần hành động cụ thể ngay hôm nay hoặc trong 1-2 ngày
  (horizon ≤ 2 ngày, assumption test hôm nay, lesson chưa replay sau >7 ngày)
- WATCH: cần chú ý nhưng chưa cần quyết định ngay
  (horizon 3-7 ngày, assumption test tuần này, memory signal mới)
- DEFER: không có trigger cấp thiết
  (thesis stable, horizon > 7 ngày, không có catalyst)

Mỗi AgendaItem:
- reason: 1 câu ngắn gọn, nêu TẠI SAO hôm nay (không phải ngày khác)
- action_hint: hành động cụ thể, rõ ràng (không viết "xem xét thêm")
- Viết bằng tiếng Việt, thân thiện nhưng sắc bén

opening_line: 1 câu tổng kết ngày — có thể highlight điều quan trọng nhất.
Không cần giải thích dài. Không thêm field ngoài schema.
Trả về JSON hợp lệ theo schema DailyAgendaResult.
"""


def build_user_prompt(ctx: AgendaContext) -> str:
    lines = [f"Ngày: {ctx.today}", ""]

    if ctx.pending_decisions:
        lines.append("== DECISION HORIZONS ==")
        for d in ctx.pending_decisions:
            status = (
                f"QUÁ HẠN {abs(d.days_until_deadline)} ngày"
                if d.days_until_deadline < 0
                else f"còn {d.days_until_deadline} ngày"
            )
            pnl_str = f" | PnL: {d.pnl_pct:+.1f}%" if d.pnl_pct is not None else ""
            lines.append(
                f"- [{d.ticker}] Decision #{d.decision_id} {d.decision_type}"
                f" | Deadline: {d.deadline} ({status}){pnl_str}"
            )
        lines.append("")

    if ctx.active_theses:
        lines.append("== ACTIVE THESES ==")
        for t in ctx.active_theses:
            health = f"health={t.health_score}/100" if t.health_score else "health=N/A"
            check = f" | next_check={t.next_assumption_check}" if t.next_assumption_check else ""
            stale = (
                f" | CHƯA REVIEW {t.last_reviewed_days_ago} ngày"
                if t.last_reviewed_days_ago and t.last_reviewed_days_ago > 14
                else ""
            )
            lines.append(
                f"- [{t.ticker}] {t.thesis_title[:60]} | {health}"
                f" | active {t.days_active}d{check}{stale}"
            )
        lines.append("")

    if ctx.memory_signals:
        lines.append("== MEMORY SIGNALS ==")
        for m in ctx.memory_signals:
            lines.append(
                f"- [{m.ticker}] {m.pattern_summary} (confidence={m.confidence:.2f})"
            )
        lines.append("")

    if ctx.unreviewed_lessons_count > 0:
        lines.append(
            f"== LESSONS CHƯA ĐỌC: {ctx.unreviewed_lessons_count} bài học "
            f"từ decision replay chưa được xem lại =="
        )

    return "\n".join(lines)
