"""
Structured output schemas for all AI agents.

These Pydantic models define the contract between the AI layer and
calling segments. All structured responses from Perplexity must
parse into one of these schemas.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class Verdict(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def _coerce_confidence(v: object) -> float:
    """Coerce confidence to float, clamped to [0.0, 1.0]."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


# ---------------------------------------------------------------------------
# Thesis Review  (used by ThesisReviewAgent)
# ---------------------------------------------------------------------------


class AssumptionRecommendation(BaseModel):
    """Recommendation for a single thesis assumption."""

    assumption_id: int
    status: Literal["VALID", "WEAKENED", "INVALIDATED", "NEEDS_MONITORING"]
    evidence: str = Field(description="Evidence supporting the status assessment")
    updated_text: str = Field(
        default="",
        description="Suggested updated assumption text if revision needed",
    )
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        """Coerce legacy/alias field names from model output.

        Maps:
          target_id           → assumption_id
          recommended_status  → status  (also normalise case)
          reason / rationale  → evidence
        """
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # target_id → assumption_id
        if "assumption_id" not in d and "target_id" in d:
            d["assumption_id"] = d["target_id"]
        # recommended_status → status (normalise to upper)
        if "status" not in d and "recommended_status" in d:
            raw = str(d["recommended_status"]).upper()
            _status_map = {
                "VALID": "VALID",
                "INVALID": "INVALIDATED",
                "UNCERTAIN": "NEEDS_MONITORING",
                "WEAKENED": "WEAKENED",
                "INVALIDATED": "INVALIDATED",
                "NEEDS_MONITORING": "NEEDS_MONITORING",
            }
            d["status"] = _status_map.get(raw, "NEEDS_MONITORING")
        elif "status" in d:
            d["status"] = str(d["status"]).upper()
        # reason / rationale → evidence
        if not d.get("evidence"):
            for alias in ("reason", "rationale", "description"):
                if d.get(alias):
                    d["evidence"] = d[alias]
                    break
        if not d.get("evidence"):
            d["evidence"] = ""
        return d

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class CatalystRecommendation(BaseModel):
    """Recommendation for a single thesis catalyst."""

    catalyst_id: int
    status: Literal["ACTIVE", "TRIGGERED", "DELAYED", "CANCELLED", "NEEDS_MONITORING"]
    updated_timeline: str = Field(
        default="",
        description="Updated timeline if changed from original",
    )
    notes: str = Field(default="", description="Additional context on catalyst status")
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        """Coerce legacy/alias field names from model output.

        Maps:
          target_id           → catalyst_id
          recommended_status  → status  (also normalise case + legacy values)
          reason / rationale  → notes

        Status normalisation:
          EXPIRED / COMPLETED         → CANCELLED
          PENDING / UNCERTAIN / WATCH → NEEDS_MONITORING
          anything else unknown       → ACTIVE  (safe fallback for catalysts)
        """
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # target_id → catalyst_id
        if "catalyst_id" not in d and "target_id" in d:
            d["catalyst_id"] = d["target_id"]
        # recommended_status → status
        if "status" not in d and "recommended_status" in d:
            raw = str(d["recommended_status"]).upper()
            _status_map = {
                "ACTIVE": "ACTIVE",
                "TRIGGERED": "TRIGGERED",
                "DELAYED": "DELAYED",
                "CANCELLED": "CANCELLED",
                "EXPIRED": "CANCELLED",
                "COMPLETED": "CANCELLED",
                "NEEDS_MONITORING": "NEEDS_MONITORING",
                "PENDING": "NEEDS_MONITORING",
                "UNCERTAIN": "NEEDS_MONITORING",
                "WATCH": "NEEDS_MONITORING",
            }
            d["status"] = _status_map.get(raw, "ACTIVE")
        elif "status" in d:
            raw = str(d["status"]).upper()
            _status_map = {
                "ACTIVE": "ACTIVE",
                "TRIGGERED": "TRIGGERED",
                "DELAYED": "DELAYED",
                "CANCELLED": "CANCELLED",
                "EXPIRED": "CANCELLED",
                "COMPLETED": "CANCELLED",
                "NEEDS_MONITORING": "NEEDS_MONITORING",
                "PENDING": "NEEDS_MONITORING",
                "UNCERTAIN": "NEEDS_MONITORING",
                "WATCH": "NEEDS_MONITORING",
            }
            d["status"] = _status_map.get(raw, raw)  # keep unknown as-is → Literal will catch
        # reason / rationale → notes
        if not d.get("notes"):
            for alias in ("reason", "rationale", "description"):
                if d.get(alias):
                    d["notes"] = d[alias]
                    break
        return d

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class ThesisReviewOutput(BaseModel):
    """Structured output from ThesisReviewAgent.

    Owner: ai segment.
    Triggered by: manual /review command or scheduled weekly review.
    Distinct from ThesisJudgeOutput: full deep review vs fast signal cross-check.
    """

    overall_verdict: Verdict
    conviction_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Updated conviction score for the thesis (0.0–1.0)",
    )
    assumption_recommendations: list[AssumptionRecommendation] = Field(
        default_factory=list
    )
    catalyst_recommendations: list[CatalystRecommendation] = Field(
        default_factory=list
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="Current key risks to monitor",
    )
    action_recommendation: Literal[
        "HOLD", "ADD", "REDUCE", "EXIT", "WAIT_FOR_CATALYST"
    ] = Field(description="Recommended portfolio action")
    summary: str = Field(description="2-3 sentence summary of thesis health")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="AI confidence in this review (0.0–1.0)",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        """Coerce legacy/alias field names from model output.

        Maps (backward-safe — only applied when canonical field is absent):
          verdict             → overall_verdict
          confidence          → conviction_score  (when conviction_score missing)
          reasoning           → summary  (when summary missing)
          risk_signals        → key_risks  (when key_risks missing)
          action_recommendation default  → HOLD  (when field missing)
        """
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # verdict → overall_verdict
        if "overall_verdict" not in d and "verdict" in d:
            d["overall_verdict"] = d["verdict"]
        # confidence → conviction_score (only when conviction_score absent)
        if "conviction_score" not in d and "confidence" in d:
            d["conviction_score"] = d["confidence"]
        # reasoning / reason → summary
        if not d.get("summary"):
            for alias in ("reasoning", "reason"):
                if d.get(alias):
                    d["summary"] = d[alias]
                    break
        # risk_signals → key_risks
        if not d.get("key_risks") and d.get("risk_signals"):
            d["key_risks"] = d["risk_signals"]
        # action_recommendation default
        if "action_recommendation" not in d:
            d["action_recommendation"] = "HOLD"
        return d

    @field_validator("conviction_score", "confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("overall_verdict", mode="before")
    @classmethod
    def normalise_verdict(cls, v: object) -> object:
        """Normalise verdict to uppercase — model sometimes returns lowercase."""
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("assumption_recommendations", "catalyst_recommendations", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Action Queue  (used by BriefingAgent)
# ---------------------------------------------------------------------------


class MarketSentiment(StrEnum):
    """Market sentiment values used by BriefingAgent.

    Canonical values (AI prompt instructs these): RISK_ON, RISK_OFF, MIXED, UNCERTAIN.
    Legacy values (kept for backward compat with old BriefSnapshot DB records): BULLISH, BEARISH, NEUTRAL.
    """

    # Canonical — AI prompt: brief.py instructs "RISK_ON | RISK_OFF | MIXED | UNCERTAIN"
    RISK_ON   = "RISK_ON"
    RISK_OFF  = "RISK_OFF"
    MIXED     = "MIXED"
    UNCERTAIN = "UNCERTAIN"
    # Legacy — kept for backward compat; do not use in new prompts
    BULLISH   = "BULLISH"
    BEARISH   = "BEARISH"
    NEUTRAL   = "NEUTRAL"


class ActionPriority(StrEnum):
    ACT_TODAY = "ACT_TODAY"
    WATCH_MORE = "WATCH_MORE"
    SKIP_TODAY = "SKIP_TODAY"


class PrioritizedAction(BaseModel):
    """A single prioritized action item in a brief."""

    ticker: str = Field(default="", description="Ticker symbol if applicable")
    priority: ActionPriority
    action: str = Field(description="Short action description")
    rationale: str = Field(description="Why this action at this priority")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    # NOTE: 'reason' is an alias for 'rationale' consumed by formatter.py.
    # formatter.py reads a.reason; Pydantic exposes it via property below.
    @property
    def reason(self) -> str:
        return self.rationale

    @model_validator(mode="before")
    @classmethod
    def coerce_reason_to_rationale(cls, data: Any) -> Any:
        """AI sometimes returns 'reason' instead of 'rationale', or null ticker.

        Coerce before field validation so Pydantic does not raise errors.
        Maps:
          ticker: null → ""  (market-level actions with no specific ticker)
          reason → rationale (only when rationale is absent)
          explanation / why → rationale (legacy aliases)
        """
        if not isinstance(data, dict):
            return data
        # Coerce null ticker → empty string
        if data.get("ticker") is None:
            data = dict(data)
            data["ticker"] = ""
        # reason / explanation / why → rationale
        if "rationale" not in data or not data.get("rationale"):
            for alias in ("reason", "explanation", "why"):
                if data.get(alias):
                    data = dict(data)
                    data["rationale"] = data[alias]
                    break
        return data

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class ActionQueue(BaseModel):
    """Derived action queue from BriefOutput.prioritized_actions.

    Populated by build_action_queue model_validator in BriefOutput.
    Not filled directly by AI.
    """

    queue: list[PrioritizedAction] = Field(
        default_factory=list,
        description=(
            "Sorted actions: ACT_TODAY → WATCH_MORE → SKIP_TODAY. "
            "Max 5. Tiebreak by confidence descending."
        ),
    )
    top_action: PrioritizedAction | None = Field(
        default=None,
        description="ACT_TODAY item with highest confidence; None if no ACT_TODAY.",
    )
    signal_summary: str = Field(
        default="",
        description=(
            "Summary string: '🔴 <urgent tickers>  🟡 <watch tickers>'. "
            "Fallback: '0 urgent / 0 watch'."
        ),
    )


class WatchlistTickerSummary(BaseModel):
    """Per-ticker summary in a brief."""

    ticker: str
    price: float = Field(default=0.0, description="Current price")
    change_pct: float = Field(default=0.0, description="Price change % today")
    signal: str = Field(default="neutral", description="bullish | bearish | neutral")
    one_line: str = Field(description="One-sentence summary for this ticker")
    watch_reason: str = Field(default="", description="Why this is on watchlist")
    verdict: Verdict = Verdict.NEUTRAL
    one_liner: str = Field(default="", description="Alias for one_line (legacy)")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class PortfolioPositionBrief(BaseModel):
    """Brief summary of a single portfolio position."""

    ticker: str
    pnl_pct: float | None = None
    verdict: Verdict = Verdict.NEUTRAL
    note: str = Field(default="", description="Short portfolio-level observation")


class BriefOutput(BaseModel):
    """Structured output from BriefingAgent (morning or EOD)."""

    headline: str = Field(description="One-sentence market headline")
    sentiment: MarketSentiment
    summary: str = Field(description="2-3 sentence narrative summary")
    key_movers: list[str] = Field(default_factory=list, description="Notable tickers or sectors")
    watchlist_alerts: list[str] = Field(
        default_factory=list,
        description="Watchlist-specific observations",
    )
    # Deprecated: kept for backward compat with old BriefSnapshot records and bot renders.
    # New code should read prioritized_actions instead.
    # Do NOT add new callers referencing this field — remove when bot fully migrated.
    action_items: list[str] = Field(
        default_factory=list,
        description="[DEPRECATED] Flat action list. Superseded by prioritized_actions.",
    )
    prioritized_actions: list[PrioritizedAction] = Field(
        default_factory=list,
        description=(
            "Hành động phân loại theo priority: ACT_TODAY → WATCH_MORE → SKIP_TODAY. "
            "AI phải xuất ít nhất 1 item khi có watchlist. "
            "Formatter nhóm theo bucket và render thành 3 section Discord."
        ),
    )
    ticker_summaries: list[WatchlistTickerSummary] = Field(
        default_factory=list,
        description="Per-ticker summary for each watchlist item",
    )
    portfolio_summary: list[str] = Field(
        default_factory=list,
        description=(
            "Nhận xét portfolio alignment với market hôm nay. "
            "Mỗi item là 1 câu liên tục: rủi ro tập trung, position nổi bật, "
            "hoặc gợi ý cần chú ý. Rỗng nếu không có portfolio data."
        ),
    )
    action_queue: ActionQueue = Field(
        default_factory=ActionQueue,
        description=(
            "Derived từ prioritized_actions — không do AI điền trực tiếp. "
            "Populated bởi build_action_queue model_validator. "
            "Downstream (bot, formatter, readmodel) dùng field này thay vì "
            "tự sort/filter prioritized_actions."
        ),
    )

    @field_validator("prioritized_actions", mode="before")
    @classmethod
    def ensure_prioritized_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def build_action_queue(self) -> "BriefOutput":
        """Derive ActionQueue từ prioritized_actions sau khi AI parse xong."""
        actions = self.prioritized_actions
        if not actions:
            return self

        _priority_order = {
            ActionPriority.ACT_TODAY: 0,
            ActionPriority.WATCH_MORE: 1,
            ActionPriority.SKIP_TODAY: 2,
        }
        sorted_actions = sorted(
            actions,
            key=lambda a: (_priority_order.get(a.priority, 9), -a.confidence),
        )
        queue = sorted_actions[:5]

        top_action = next(
            (a for a in queue if a.priority == ActionPriority.ACT_TODAY), None
        )

        urgent = [a.ticker for a in queue if a.priority == ActionPriority.ACT_TODAY and a.ticker]
        watch = [a.ticker for a in queue if a.priority == ActionPriority.WATCH_MORE and a.ticker]
        if urgent or watch:
            urgent_str = " ".join(urgent) if urgent else f"{len(urgent)} urgent"
            watch_str = " ".join(watch) if watch else f"{len(watch)} watch"
            signal_summary = f"\U0001f534 {urgent_str}  \U0001f7e1 {watch_str}"
        else:
            act_count = sum(1 for a in queue if a.priority == ActionPriority.ACT_TODAY)
            watch_count = sum(1 for a in queue if a.priority == ActionPriority.WATCH_MORE)
            signal_summary = f"\U0001f534 {act_count} urgent  \U0001f7e1 {watch_count} watch"

        self.action_queue = ActionQueue(
            queue=queue,
            top_action=top_action,
            signal_summary=signal_summary,
        )
        return self


# ---------------------------------------------------------------------------
# Stock Analysis  (used by general InvestorAgent / legacy callers)
# ---------------------------------------------------------------------------


class StockAnalysisOutput(BaseModel):
    """Structured output from general InvestorAgent for a single ticker."""

    ticker: str
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    price_target_note: str = Field(default="", description="Qualitative note on price target")
    key_positives: list[str] = Field(default_factory=list)
    key_negatives: list[str] = Field(default_factory=list)
    summary: str


# ---------------------------------------------------------------------------
# Proactive Alert  (used by ProactiveAlertAgent)
# ---------------------------------------------------------------------------


class RiskSignal(BaseModel):
    """Một risk signal cụ thể liên quan đến mã chứng khoán."""

    description: str = Field(description="Mô tả rủi ro cụ thể, tiếng Việt")
    severity: Literal["LOW", "MEDIUM", "HIGH"]


class ProactiveAlertOutput(BaseModel):
    """Structured output từ AIClient cho mỗi SignalDetectedEvent.

    Owner: ai segment.
    Consumed by: ProactiveAlertAgent → RecommendationReadyEvent → bot/api.

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


# ---------------------------------------------------------------------------
# Thesis Suggestion  (used by ThesisSuggestAgent)
# ---------------------------------------------------------------------------


class SuggestedAssumption(BaseModel):
    assumption_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class SuggestedCatalyst(BaseModel):
    catalyst_text: str
    expected_timeline: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class ThesisSuggestionResult(BaseModel):
    """Structured output from ThesisSuggestAgent.

    Owner: ai segment.
    Public contract returned to callers (api, bot, thesis segment).
    """

    ticker: str
    title: str
    thesis_type: str = Field(
        default="",
        description="Loại thesis: VALUE, GROWTH, TURNAROUND, TECHNICAL, MACRO",
    )
    summary: str
    assumptions: list[SuggestedAssumption] = Field(default_factory=list)
    catalysts: list[SuggestedCatalyst] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    target_horizon: str = Field(
        default="",
        description="Khung thời gian kỳ vọng: SHORT (< 3 tháng), MEDIUM (3-12 tháng), LONG (> 12 tháng)",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("assumptions", "catalysts", "invalidation_conditions", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Why Analysis  (used by WhyAgent)
# ---------------------------------------------------------------------------


class MovementDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class WhyOutput(BaseModel):
    """Structured output from WhyAgent — explains price movement.

    Owner: ai segment.
    change_pct is NOT an AI field — it is injected by WhyService from the
    live quote and attached after AI parse. Embed consumers read it from
    the tuple returned by WhyService.explain().
    """

    ticker: str
    direction: MovementDirection
    magnitude_pct: float = Field(description="Estimated magnitude of movement in %")
    primary_cause: str = Field(description="Main reason for the movement")
    contributing_factors: list[str] = Field(
        default_factory=list,
        description="Secondary contributing factors",
    )
    market_context: str = Field(
        default="",
        description="Broader market context relevant to this movement",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Rủi ro hoặc yếu tố cần theo dõi sau biến động này (tối đa 3)",
    )
    data_quality: str = Field(
        default="",
        description=(
            "Ghi chú chất lượng dữ liệu: thiếu OHLCV, thiếu tin tức, confidence thấp. "
            "Để trống nếu dữ liệu đầy đủ."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


# ---------------------------------------------------------------------------
# Pre-Trade Check  (used by PreTradeAgent)
# ---------------------------------------------------------------------------


class TradeDecision(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    REDUCE = "REDUCE"
    HOLD = "HOLD"


class AlignmentStatus(StrEnum):
    ALIGNED = "ALIGNED"
    NEUTRAL = "NEUTRAL"
    MISALIGNED = "MISALIGNED"


class ResolutionCategory(StrEnum):
    THESIS_CONFLICT = "THESIS_CONFLICT"
    RISK_LIMIT = "RISK_LIMIT"
    TIMING = "TIMING"
    MARKET_CONDITION = "MARKET_CONDITION"
    PORTFOLIO_BALANCE = "PORTFOLIO_BALANCE"


class ResolutionStep(BaseModel):
    """A single resolution step for a pre-trade conflict."""

    category: ResolutionCategory
    issue: str = Field(description="Specific issue identified")
    resolution: str = Field(description="Recommended resolution")
    priority: Literal["BLOCKING", "HIGH", "MEDIUM", "LOW"] = Field(
        default="MEDIUM",
        description="Priority of resolving this issue before trading",
    )


class PreTradeCheckOutput(BaseModel):
    """Structured output from PreTradeAgent.

    Owner: ai segment.
    Triggered by: manual /pretrade command before placing an order.
    """

    ticker: str
    intended_action: TradeDecision
    alignment: AlignmentStatus = Field(
        description="How well the trade aligns with existing thesis and strategy"
    )
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    proceed_recommendation: bool = Field(
        description="True if AI recommends proceeding with the trade"
    )
    blocking_issues: list[str] = Field(
        default_factory=list,
        description="Issues that must be resolved before trading",
    )
    resolution_steps: list[ResolutionStep] = Field(
        default_factory=list,
        description="Ordered steps to resolve blocking issues",
    )
    summary: str = Field(description="2-3 sentence pre-trade assessment")


# ---------------------------------------------------------------------------
# Decision Replay  (used by ReplayAgent prompt — schema_block only)
# ---------------------------------------------------------------------------


class ReplayOutput(BaseModel):
    """Schema used ONLY for schema_block() injection into ReplayAgent system prompt.

    Actual AI response is parsed into DecisionReplayResult (in replay.py agent).
    These fields must mirror DecisionReplayResult exactly.

    Owner: ai segment.
    Consumed by: src/ai/prompts/replay.py → schema_block() → SYSTEM_PROMPT injection.
    NOT used for direct AI response parsing.
    """

    decision_id: int
    ticker: str
    decision_type: str = Field(description="BUY | SELL | HOLD | ADD | REDUCE")
    outcome_verdict: str = Field(description="CORRECT | INCORRECT | MIXED")
    what_went_right: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    key_lesson: str
    pattern_detected: str | None = None
    suggested_adjustment: str | None = None
    confidence: str = Field(description="HIGH | MEDIUM | LOW")
