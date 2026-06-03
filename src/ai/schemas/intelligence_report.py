"""
src/ai/schemas/intelligence_report.py

IntelligenceReport — contract trung tâm của Investor OS.

Owner: ai segment.
Consumers: core/engine.py (writer), readmodel, bot, api (readers).

Design principles:
  - Aggregates outputs từ các agents đã chạy trong một cycle.
  - Không replace individual agent schemas — chỉ tổng hợp.
  - Mỗi agent slot là Optional: agent có thể không chạy (chưa đủ signal).
  - `trigger_source` giúp readmodel / bot biết cycle này do gì khởi động.
  - `schema_version` bắt buộc — readmodel dùng để detect stale cache.
  - `cycle_id` là UUID string — correlation ID xuyên suốt log / trace.

Downstream usage pattern:
    report = IntelligenceReport(...)
    # bot: render report.top_verdict + report.priority_actions
    # readmodel: upsert by (user_id, cycle_id)
    # feedback: ghi lại user_action với cycle_id để trace

Mapping từ agent schemas:
    SignalEngineOutput.ranked_signals  → risk_flags + next_watch_tickers
    ThesisJudgeOutput.verdict          → top_verdict (voting)
    InvalidationVerdict                → risk_flags[THESIS_INVALIDATED]
    NextActionPlan.actions             → priority_actions
    PortfolioRiskNarrativeOutput       → risk_flags[CONCENTRATION_RISK]
    BriefOutput                        → narrative_summary
    ReplayOutput                       → risk_flags[PATTERN_BIAS_WARNING]
    VerdictOutput                      → top_verdict (voting weight)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Trigger sources
# ---------------------------------------------------------------------------

TRIGGER_SOURCES = Literal[
    "scheduler_morning",    # briefing sáng
    "scheduler_eod",        # end-of-day summary
    "watchlist_alert",      # alert kích hoạt từ watchlist
    "user_query",           # user hỏi trực tiếp qua bot/api
    "thesis_invalidated",   # thesis bị invalidate → emergency cycle
    "portfolio_breach",     # risk breach → emergency cycle
    "manual",               # dev trigger / testing
]

AGENT_SLOT_STATUS = Literal[
    "ran",           # agent chạy và có output
    "skipped",       # không đủ signal để chạy
    "failed",        # chạy nhưng raise exception
    "not_triggered", # trigger không yêu cầu agent này
]


# ---------------------------------------------------------------------------
# AgentSlot — wrapper bọc output của từng agent
# ---------------------------------------------------------------------------

class AgentSlot(BaseModel):
    """Wrapper cho output của một agent trong một cycle.

    Dùng để engine biết agent nào đã chạy, kết quả gì,
    mà không cần parse raw output của từng agent.
    """

    agent_name: str = Field(description="Tên agent, VD: 'thesis_judge', 'signal_engine'")
    status: AGENT_SLOT_STATUS = Field(default="not_triggered")
    output: dict | None = Field(
        default=None,
        description=(
            "Raw output dict của agent — serialized từ Pydantic model. "
            "None khi status != 'ran'."
        ),
    )
    ran_at: datetime | None = Field(default=None)
    error_summary: str | None = Field(
        default=None,
        description="Tóm tắt lỗi khi status='failed'. Không expose stack trace.",
    )


# ---------------------------------------------------------------------------
# PriorityAction — action item cụ thể cho user
# ---------------------------------------------------------------------------

class PriorityAction(BaseModel):
    """Một hành động cụ thể user nên làm sau khi đọc briefing.

    Tổng hợp từ: next_action_suggester, thesis_judge, invalidation_detector.
    Không thay thế các schema đó — chỉ extract phần actionable.
    """

    rank: int = Field(ge=1, le=10, description="Thứ tự ưu tiên, 1 = cao nhất.")
    ticker: str | None = Field(default=None)
    action_type: Literal[
        "REVIEW_THESIS",
        "CHECK_STOP_LOSS",
        "CONSIDER_EXIT",
        "CONSIDER_ENTRY",
        "MONITOR",
        "NO_ACTION",
    ]
    urgency: Literal["immediate", "today", "this_week"]
    instruction: str = Field(
        max_length=200,
        description=(
            "Câu lệnh cụ thể. Bắt đầu bằng động từ. "
            "VD: 'Kiểm tra SL của VHM tại 44.5k'"
        ),
    )
    source_agent: str = Field(description="Agent nào tạo ra action này.")
    reasoning: str = Field(max_length=150)


# ---------------------------------------------------------------------------
# RiskFlag — cảnh báo rủi ro tổng hợp
# ---------------------------------------------------------------------------

class RiskFlag(BaseModel):
    """Cờ rủi ro đã được cross-validate giữa các agents."""

    flag_type: Literal[
        "THESIS_INVALIDATED",
        "STOP_LOSS_BREACH",
        "CONCENTRATION_RISK",
        "SECTOR_ROTATION_ADVERSE",
        "VOLUME_ANOMALY",
        "PATTERN_BIAS_WARNING",   # từ replay_agent / memory
        "MARKET_TREND_REVERSAL",
    ]
    ticker: str | None = None
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    description: str = Field(max_length=200)
    confirmed_by: list[str] = Field(
        default_factory=list,
        description="Danh sách agents đã confirm flag này (cross-validation).",
    )
    is_new: bool = Field(
        default=True,
        description="False nếu flag này đã xuất hiện trong cycle trước — tránh spam.",
    )


# ---------------------------------------------------------------------------
# IntelligenceReport — contract trung tâm
# ---------------------------------------------------------------------------

class IntelligenceReport(BaseModel):
    """Tổng hợp toàn bộ output của một AI cycle.

    Không phải AI output trực tiếp — là envelope do engine.py tạo
    sau khi dispatch + synthesize xong tất cả agents.

    Consumers:
        readmodel  → cache/upsert, serve API
        bot        → render Discord message
        api        → serve /v1/intelligence endpoint
        briefing   → narrative_builder nhận report này làm input
        feedback   → ghi cycle_id vào user_action để trace
    """

    # --- Identity ---
    cycle_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID của cycle này. Dùng làm correlation ID.",
    )
    schema_version: str = Field(
        default="1.0",
        description=(
            "Version của IntelligenceReport schema. "
            "readmodel phải check field này trước khi deserialize."
        ),
    )
    user_id: str
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    trigger_source: TRIGGER_SOURCES

    # --- Top-level verdict ---
    top_verdict: Literal[
        "BUY_SIGNAL",
        "SELL_SIGNAL",
        "HOLD",
        "REVIEW_THESIS",
        "RISK_ALERT",
        "NO_ACTION",
    ] = Field(
        description=(
            "Verdict tổng hợp từ cross-agent synthesis. "
            "Không phải output của một agent đơn lẻ. "
            "engine.py tính dựa trên voting / urgency_score."
        ),
    )
    top_verdict_conviction: Literal["high", "medium", "low"]
    overall_confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    # --- Priority actions (render trực tiếp cho user) ---
    priority_actions: list[PriorityAction] = Field(
        default_factory=list,
        max_length=5,
        description="Tối đa 5 actions, sorted by rank. Bot render list này.",
    )

    # --- Risk flags (tổng hợp, cross-validated) ---
    risk_flags: list[RiskFlag] = Field(
        default_factory=list,
        max_length=10,
    )

    # --- Watch list (tickers cần theo dõi tiếp) ---
    next_watch_tickers: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Dedup từ next_watch_items của các agents.",
    )

    # --- Narrative (human-readable, từ briefing agent) ---
    narrative_summary: str = Field(
        default="",
        max_length=800,
        description=(
            "1–3 đoạn ngắn tóm tắt toàn bộ cycle. "
            "Viết cho nhà đầu tư đọc, không phải cho AI. "
            "Sinh bởi briefing.py agent SAU KHI có IntelligenceReport."
        ),
    )

    # --- Agent slots (audit trail đầy đủ) ---
    agent_slots: list[AgentSlot] = Field(
        default_factory=list,
        description=(
            "Danh sách agents đã được dispatch trong cycle này. "
            "Dùng để debug, trace, và readmodel biết agent nào đã chạy."
        ),
    )

    # --- Metadata ---
    is_emergency_cycle: bool = Field(
        default=False,
        description="True khi trigger_source là thesis_invalidated hoặc portfolio_breach.",
    )
    ttl_minutes: int = Field(
        default=60,
        description=(
            "readmodel invalidate cache sau bao nhiêu phút. "
            "Emergency cycles: 15 phút. Morning brief: 240 phút."
        ),
    )

    # --- Validators ---

    @field_validator("overall_confidence", mode="before")
    @classmethod
    def _clamp(cls, v: object) -> float:
        try:
            return max(0.0, min(1.0, float(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5

    @model_validator(mode="after")
    def _set_emergency_flag(self) -> "IntelligenceReport":
        if self.trigger_source in ("thesis_invalidated", "portfolio_breach"):
            self.is_emergency_cycle = True
            self.ttl_minutes = 15
        return self

    @model_validator(mode="after")
    def _sort_priority_actions(self) -> "IntelligenceReport":
        self.priority_actions = sorted(self.priority_actions, key=lambda a: a.rank)
        return self

    # --- Convenience methods (dùng bởi bot/api, không bởi agents) ---

    def top_risk_flags(self, severity: str = "HIGH") -> list[RiskFlag]:
        """Return risk flags có severity >= threshold."""
        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        threshold = order.get(severity, 1)
        return [f for f in self.risk_flags if order.get(f.severity, 3) <= threshold]

    def has_immediate_actions(self) -> bool:
        """True nếu có ít nhất 1 action urgency='immediate'."""
        return any(a.urgency == "immediate" for a in self.priority_actions)

    def get_slot(self, agent_name: str) -> AgentSlot | None:
        """Lookup AgentSlot theo tên agent."""
        return next((s for s in self.agent_slots if s.agent_name == agent_name), None)

    def to_bot_summary(self) -> str:
        """Render nhanh cho Discord bot — pure method, không gọi AI."""
        verdict_emoji = {
            "BUY_SIGNAL": "🟢",
            "SELL_SIGNAL": "🔴",
            "RISK_ALERT": "⚠️",
            "REVIEW_THESIS": "🔍",
            "HOLD": "⏸️",
            "NO_ACTION": "✅",
        }
        emoji = verdict_emoji.get(self.top_verdict, "📊")
        lines = [
            f"{emoji} **{self.top_verdict}** ({self.top_verdict_conviction})",
            f"Confidence: {self.overall_confidence:.0%}",
        ]
        if self.priority_actions:
            lines.append("\n**Cần làm:**")
            for a in self.priority_actions[:3]:
                urgency_tag = "🚨" if a.urgency == "immediate" else "📌"
                lines.append(f"{urgency_tag} {a.instruction}")
        if critical := self.top_risk_flags("HIGH"):
            lines.append("\n**Rủi ro:**")
            for f in critical[:2]:
                lines.append(f"• {f.description}")
        return "\n".join(lines)
