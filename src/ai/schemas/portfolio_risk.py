"""
Portfolio Risk Narrator schema.

Owner: ai segment.
Used by: PortfolioRiskNarratorAgent.

Input contract  : PortfolioRiskNote (from signal_engine.py)
                  + SignalEngineOutput ranked_signals / risk_alerts
                  + optional StressTestOutput portfolio_impact_note
Output feeds    : BriefOutput.portfolio_narrative (Wave 2 — briefing.py)
                  bot /portfolio command (adapter)
                  readmodel portfolio risk view (read concern)

Design principle: AI reads structured rule-based context (PortfolioRiskNote)
and writes a *narrative* organised by risk theme — not a flat bullet list.
Max 4 chapters, sorted severity DESC. Downstream consumers read chapters
sequentially and render as Discord embeds or API JSON.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.ai.schemas._base import RiskLevel, Verdict, _coerce_confidence


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskTheme(StrEnum):
    """Canonical risk themes for portfolio-level narrative chapters."""

    CONCENTRATION     = "CONCENTRATION"      # single ticker or sector > threshold
    THESIS_DRIFT      = "THESIS_DRIFT"       # price moving against active thesis direction
    DRAWDOWN          = "DRAWDOWN"           # position PnL or total PnL below threshold
    SECTOR_OVEREXPOSE = "SECTOR_OVEREXPOSE"  # >40% NAV in one sector
    CASH_DRAG         = "CASH_DRAG"          # cash ratio too high during uptrend
    SIGNAL_CONFLICT   = "SIGNAL_CONFLICT"    # conflicting signals on the same ticker


# ---------------------------------------------------------------------------
# Chapter — one risk theme per chapter
# ---------------------------------------------------------------------------


class RiskChapter(BaseModel):
    """One 'chapter' in the portfolio risk narrative.

    Each chapter represents a distinct risk theme identified in the portfolio.
    Chapters are sorted by severity DESC in PortfolioRiskNarrativeOutput.
    """

    theme: RiskTheme
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    headline: str = Field(
        description=(
            "1 câu mô tả rủi ro, tiếng Việt, có thể đọc và hành động ngay. "
            "Ví dụ: 'VHM chiếm 31% NAV — concentration risk nếu BDS tiếp tục điều chỉnh.'"
        )
    )
    affected_tickers: list[str] = Field(
        default_factory=list,
        description="Danh sách ticker liên quan đến risk theme này.",
    )
    evidence: str = Field(
        description=(
            "Dữ liệu cụ thể hỗ trợ headline. "
            "Ví dụ: 'VHM weight=31.2%, threshold=25%. PnL hiện tại -3.1%.'"
        )
    )
    suggested_action: str = Field(
        description=(
            "Hành động gợi ý ngắn gọn, specific. "
            "Ví dụ: 'Trim VHM xuống ~20% nếu giá không giữ được 42,000.'"
        )
    )

    @field_validator("affected_tickers", mode="before")
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Top-level narrative output
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


class PortfolioRiskNarrativeOutput(BaseModel):
    """Structured output from PortfolioRiskNarratorAgent.

    Owner: ai segment.
    Not AI-free: AI reads PortfolioRiskNote + SignalEngineOutput and writes
    the narrative. Rule-based pre-processing (PortfolioRiskNote) feeds this
    agent — AI is responsible only for the narrative layer on top.

    Downstream contract:
      - BriefOutput.portfolio_narrative: PortfolioRiskNarrativeOutput | None
        (Wave 2 — add optional field to briefing.py)
      - Bot /portfolio command renders opening_line + chapters as embeds
      - ReadModel caches risk_score for portfolio risk timeline
    """

    overall_risk_level: RiskLevel = Field(
        description="Composite risk level across all chapters."
    )
    risk_score: int = Field(
        ge=0,
        le=100,
        description=(
            "Composite risk score 0–100. "
            "0 = no material risk, 100 = critical action required today. "
            "Derived from severity distribution of chapters."
        ),
    )
    opening_line: str = Field(
        description=(
            "1 câu tóm tắt toàn bộ risk state của portfolio hôm nay. "
            "Đọc thay tiêu đề — phải standalone và actionable. "
            "Ví dụ: 'Portfolio đang chịu áp lực concentration và thesis drift — "
            "ưu tiên review VHM và HPG trước phiên chiều.'"
        )
    )
    chapters: list[RiskChapter] = Field(
        default_factory=list,
        description=(
            "Mỗi chapter = 1 risk theme. Tối đa 4. "
            "Sorted severity DESC (CRITICAL → HIGH → MEDIUM → LOW). "
            "Nếu không có risk nào đáng kể, list rỗng."
        ),
    )
    portfolio_verdict: Verdict = Field(
        description="Overall portfolio verdict based on risk profile."
    )
    immediate_actions: list[str] = Field(
        default_factory=list,
        description=(
            "Top 2–3 việc cần làm hôm nay. Specific, không chung chung. "
            "Ví dụ: 'Đặt stop-loss VHM tại 41,500' hoặc 'Review thesis HPG — "
            "assumption về margin recovery chưa có catalyst rõ ràng.'"
        ),
    )
    watch_next_session: list[str] = Field(
        default_factory=list,
        description=(
            "Mốc cụ thể cần quan sát phiên tới. Tối đa 3. "
            "Ví dụ: 'VN-Index giữ được 1,270 không?', 'VCB foreign buy/sell flow.'"
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="AI confidence in this narrative (0.0–1.0).",
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("risk_score", mode="before")
    @classmethod
    def coerce_score(cls, v: object) -> int:
        try:
            return max(0, min(100, int(float(v))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    @field_validator("chapters", "immediate_actions", "watch_next_session", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def sort_and_cap_chapters(self) -> "PortfolioRiskNarrativeOutput":
        """Sort chapters by severity DESC and cap at 4."""
        if self.chapters:
            self.chapters = sorted(
                self.chapters,
                key=lambda c: _SEVERITY_ORDER.get(c.severity, 9),
            )[:4]
        return self
