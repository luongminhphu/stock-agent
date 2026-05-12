"""
Structured output schemas for all AI agents.

These Pydantic models define the contract between the AI layer and
calling segments. All structured responses from Perplexity must
parse into one of these schemas.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class Verdict(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    WATCHLIST = "WATCHLIST"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------


def _coerce_confidence(v: object) -> float:
    """Coerce AI confidence output to float 0.0-1.0.

    Handles three shapes returned in practice:
      - float 0.0-1.0  → pass through
      - int/float >1.0 → divide by 100 (AI used 0-100 scale)
      - str 'HIGH' | 'MEDIUM' | 'LOW' → map to 0.85 / 0.60 / 0.35
      - anything else  → 0.0
    """
    _STR_MAP = {"HIGH": 0.85, "MEDIUM": 0.60, "LOW": 0.35}
    if isinstance(v, str):
        return _STR_MAP.get(v.upper(), 0.0)
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return f / 100.0 if f > 1.0 else f


# ---------------------------------------------------------------------------
# Thesis Review — AI recommendations (user must confirm before applying)
# ---------------------------------------------------------------------------


class AssumptionRecommendation(BaseModel):
    """AI gợi ý cập nhật status cho một assumption cụ thể.

    target_id phải khớp với Assumption.id trong DB.
    Chỉ là đề xuất — không tự apply, cần user xác nhận.
    """

    target_id: int = Field(description="Assumption.id cần cập nhật")
    description: str = Field(description="Mô tả assumption để user nhận diện")
    recommended_status: str = Field(description="Status đề xuất: valid | invalid | uncertain")
    reason: str = Field(description="Lý do AI đề xuất status này")


class CatalystRecommendation(BaseModel):
    """AI gợi ý cập nhật status cho một catalyst cụ thể.

    target_id phải khớp với Catalyst.id trong DB.
    Chỉ là đề xuất — không tự apply, cần user xác nhận.
    """

    target_id: int = Field(description="Catalyst.id cần cập nhật")
    description: str = Field(description="Mô tả catalyst để user nhận diện")
    recommended_status: str = Field(description="Status đề xuất: triggered | expired | cancelled")
    reason: str = Field(description="Lý do AI đề xuất status này")


# ---------------------------------------------------------------------------
# Thesis Review
# ---------------------------------------------------------------------------


def _coerce_str_list(v: object, key: str) -> list[object]:
    """Extract ``key`` from each dict item, falling back to str(item).

    Shared helper for risk_signals and next_watch_items validators — both
    suffer from the same sonar-pro habit of returning structured dicts
    instead of plain strings.
    """
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list):
        return []
    coerced: list[object] = []
    for item in v:
        if isinstance(item, dict):
            coerced.append(item.get(key) or str(item))
        else:
            coerced.append(item)
    return coerced


class ThesisReviewOutput(BaseModel):
    """Structured output from ThesisReviewAgent."""

    verdict: Verdict
    confidence: float = Field(
        ge=0.0, le=1.0, description="0.0 = no confidence, 1.0 = very confident"
    )
    risk_signals: list[str] = Field(default_factory=list, description="Key risks detected")
    next_watch_items: list[str] = Field(default_factory=list, description="What to monitor next")
    reasoning: str = Field(description="Natural language explanation of the verdict")
    assumption_recommendations: list[AssumptionRecommendation] = Field(
        default_factory=list,
        description=(
            "AI gợi ý cập nhật status cho từng assumption. "
            "Chỉ là đề xuất — ReviewService persist dưới dạng PENDING, "
            "user phải xác nhận trước khi apply."
        ),
    )
    catalyst_recommendations: list[CatalystRecommendation] = Field(
        default_factory=list,
        description=(
            "AI gợi ý cập nhật status cho từng catalyst. "
            "Chỉ là đề xuất — ReviewService persist dưới dạng PENDING, "
            "user phải xác nhận trước khi apply."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_field_names(cls, data: object) -> object:
        """Rename sonar-pro alias fields to canonical names before field validation.

        sonar-pro occasionally uses:
          confidence_pct (int 0-100) instead of confidence

        Renaming here ensures the existing coerce_confidence field_validator
        (which normalises >1.0 values) handles the value correctly.
        """
        if not isinstance(data, dict):
            return data
        if "confidence" not in data and "confidence_pct" in data:
            data["confidence"] = data.pop("confidence_pct")
        return data

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: object) -> float:
        """Coerce AI int scale (0-100) to float (0.0-1.0).

        sonar-pro sometimes returns confidence as an integer 0-100
        instead of the specified 0.0-1.0 float. Divide by 100 when
        the value clearly exceeds the valid range.
        """
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return f / 100.0 if f > 1.0 else f

    @field_validator("risk_signals", mode="before")
    @classmethod
    def coerce_risk_signals(cls, v: object) -> list[object]:
        """Coerce list[dict] → list[str] when AI returns structured risk objects.

        sonar-pro sometimes returns risk_signals as:
            [{"signal": "...", "severity": "HIGH"}, ...]
        instead of the specified list[str]. Extract the 'signal' text;
        fall back to str(item) for any other dict shape.
        """
        return _coerce_str_list(v, "signal")

    @field_validator("next_watch_items", mode="before")
    @classmethod
    def coerce_next_watch_items(cls, v: object) -> list[object]:
        """Coerce list[dict] → list[str] when AI returns structured watch objects.

        sonar-pro sometimes returns next_watch_items as:
            [{"item": "Q1 2026 earnings...", "action": "hold/add on dips."}, ...]
        instead of the specified list[str]. Extract the 'item' text;
        fall back to str(item) for any other dict shape.
        """
        return _coerce_str_list(v, "item")

    @field_validator("assumption_recommendations", "catalyst_recommendations", mode="before")
    @classmethod
    def ensure_rec_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Market Brief
# ---------------------------------------------------------------------------


class MarketSentiment(StrEnum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    MIXED = "MIXED"
    UNCERTAIN = "UNCERTAIN"


class ActionPriority(StrEnum):
    """Priority buckets for pre-market decision brief actions."""

    ACT_TODAY  = "ACT_TODAY"   # Cần hành động trước khi mở lệnh hôm nay
    WATCH_MORE = "WATCH_MORE"  # Theo dõi thêm, chưa cần quyết định ngay
    SKIP_TODAY = "SKIP_TODAY"  # Có thể bỏ qua phiên này


class PrioritizedAction(BaseModel):
    """Một hành động được AI phân loại theo priority cho morning brief.

    Thay thế action_items: list[str] bằng cấu trúc có ticker + priority + reason.
    Formatter sẽ nhóm theo priority và render thành 3 bucket Discord section.
    """

    ticker: str | None = Field(
        default=None,
        description="Mã CK liên quan. None nếu là action market-level (vĩ mô, sentiment).",
    )
    priority: ActionPriority
    action: str = Field(
        description="Hành động cụ thể, có thể đo được. VD: 'Review stop-loss VCB trước 9h'"
    )
    reason: str = Field(
        description="Lý do ngắn gọn AI đề xuất action này. VD: 'Giá đang tiếp cận stop 82,000'"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, default=0.7,
        description="Độ tin cậy của AI với action này. Hiển thị nếu < 0.7.",
    )

    # Intelligence Spine extensions — optional, AI có thể không điền
    condition: str | None = Field(
        default=None,
        description=(
            "Điều kiện kích hoạt action này. "
            "VD: 'Chỉ nếu TCB về dưới 29,000'. None = unconditional."
        ),
    )
    if_ignored_consequence: str | None = Field(
        default=None,
        description=(
            "Hậu quả cụ thể nếu bỏ qua action này. "
            "VD: 'Holding 15% không có exit plan khi thesis đang yếu'."
        ),
    )
    causal_source: str | None = Field(
        default=None,
        description=(
            "Agent hoặc signal đã trigger action này. "
            "VD: 'stress_test:VHM', 'watchdog:TCB', 'briefing:market_context'."
        ),
    )


# ---------------------------------------------------------------------------
# Intelligence Spine — Action Queue
# ---------------------------------------------------------------------------


class ActionQueue(BaseModel):
    """Distilled action list từ prioritized_actions của BriefOutput.

    Được populate bởi BriefOutput.build_action_queue model_validator sau khi
    AI trả về — không yêu cầu AI tự điền trực tiếp.

    Downstream (bot formatter, readmodel) nên dùng field này thay vì tự
    sort/filter prioritized_actions.

    top_action: ACT_TODAY item có confidence cao nhất. None nếu không có.
    queue: full list sorted ACT_TODAY → WATCH_MORE → SKIP_TODAY, max 5.
    signal_summary: header line cho Discord. Format: "🔴 TCB, VHM  🟡 HPG"
    """

    top_action: PrioritizedAction | None = Field(default=None)
    queue: list[PrioritizedAction] = Field(default_factory=list)
    signal_summary: str = Field(
        default="",
        description=(
            "1-line summary cho bot header. "
            "Format: '🔴 <urgent tickers>  🟡 <watch tickers>'. "
            "VD: '🔴 TCB, VHM  🟡 HPG' hoặc '🔴 0 urgent  🟡 HPG, SSI'."
        ),
    )


class WatchlistTickerSummary(BaseModel):
    ticker: str
    price: float
    change_pct: float
    signal: str
    one_line: str
    watch_reason: str


class PortfolioPositionBrief(BaseModel):
    """Snapshot P&L của một position để inject vào morning brief.

    Chỉ dùng cho briefing context — không phải full PositionPnl.
    AI dùng để nhận xét portfolio alignment với market sentiment.
    """

    ticker: str
    unrealized_pct: float = Field(description="% lãi/lỗ chưa thực hiện")
    unrealized_pnl: float = Field(description="Lãi/lỗ tuyệt đối (VNĐ)")
    signal: str = Field(description="bullish | bearish | neutral — từ watchlist scan nếu có")


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
        """Derive ActionQueue từ prioritized_actions sau khi AI parse xong.

        Logic:
        - queue: sorted ACT_TODAY → WATCH_MORE → SKIP_TODAY, max 5,
                 tiebreak by confidence descending
        - top_action: ACT_TODAY item có confidence cao nhất; None nếu không có
        - signal_summary: "🔴 <urgent tickers>  🟡 <watch tickers>"
                          ticker label khi có ticker, fallback "0 urgent" / "0 watch"

        Guard: nếu prioritized_actions rỗng, giữ nguyên default ActionQueue.
        """
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
        )[:5]

        act_today = [a for a in sorted_actions if a.priority == ActionPriority.ACT_TODAY]
        watch_more = [a for a in sorted_actions if a.priority == ActionPriority.WATCH_MORE]

        top = max(act_today, key=lambda a: a.confidence) if act_today else None

        urgent_str = (
            ", ".join(a.ticker for a in act_today if a.ticker)
            or "0 urgent"
        )
        watch_str = (
            ", ".join(a.ticker for a in watch_more if a.ticker)
            or "0 watch"
        )
        signal_summary = f"🔴 {urgent_str}  🟡 {watch_str}"

        self.action_queue = ActionQueue(
            top_action=top,
            queue=sorted_actions,
            signal_summary=signal_summary,
        )
        return self


# ---------------------------------------------------------------------------
# Stock Analysis
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
# Thesis Suggestion  (used by ThesisSuggestAgent)
# ---------------------------------------------------------------------------


class SuggestedAssumption(BaseModel):
    """A single AI-suggested assumption for a thesis."""

    description: str = Field(description="Nội dung giả định then chốt")
    rationale: str = Field(description="Lý do vì sao đây là giả định quan trọng")


class SuggestedCatalyst(BaseModel):
    """A single AI-suggested catalyst for a thesis."""

    description: str = Field(description="Sự kiện / catalyst cụ thể")
    expected_timeline: str = Field(description="Khung thời gian dự kiến, VD: Q3 2025, H1 2026")
    rationale: str = Field(description="Tại sao catalyst này có thể thúc đẩy giá")


class ThesisSuggestionResult(BaseModel):
    """Structured output from ThesisSuggestAgent.

    This is a *draft* — the investor must review and confirm before saving.
    entry_price_hint / target_price_hint / stop_loss_hint are AI estimates
    and should NEVER be auto-saved without user confirmation.
    """

    ticker: str = Field(description="Mã cổ phiếu (uppercase)")
    thesis_title: str = Field(description="Tiêu đề luận điểm đầu tư ngắn gọn")
    thesis_summary: str = Field(description="Mô tả thesis 2-3 câu")
    entry_price_hint: float | None = Field(
        default=None, description="Giá vào gợi ý (VNĐ). Chỉ là hint — cần user xác nhận."
    )
    target_price_hint: float | None = Field(
        default=None, description="Giá mục tiêu gợi ý (VNĐ). Chỉ là hint."
    )
    stop_loss_hint: float | None = Field(
        default=None, description="Stop loss gợi ý (VNĐ). Chỉ là hint."
    )
    assumptions: list[SuggestedAssumption] = Field(
        default_factory=list,
        description="Danh sách 3-5 giả định then chốt",
    )
    catalysts: list[SuggestedCatalyst] = Field(
        default_factory=list,
        description="Danh sách 2-4 catalyst tiềm năng",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Mức độ tin cậy tổng thể của AI với gợi ý này (0.0-1.0)",
    )
    reasoning: str = Field(description="Lý do tổng thể vì sao AI đề xuất thesis này")

    @field_validator("assumptions", "catalysts", mode="before")
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return []
        return v  # type: ignore[return-value]


class MovementDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class WhyOutput(BaseModel):
    """Structured output from WhyAgent."""

    ticker: str
    direction: MovementDirection
    change_pct: float = Field(description="% thay đổi thực tế")
    headline: str = Field(description="1 câu tóm tắt nguyên nhân chính")
    causes: list[str] = Field(description="2-4 nguyên nhân cụ thể, theo thứ tự quan trọng")
    macro_context: str = Field(default="", description="Yếu tố vĩ mô liên quan nếu có")
    risk_flags: list[str] = Field(default_factory=list, description="Rủi ro cần theo dõi tiếp")
    confidence: float = Field(ge=0.0, le=1.0, description="Độ tin cậy phân tích")
    data_quality: str = Field(default="", description="Ghi chú về chất lượng dữ liệu đầu vào")


# ---------------------------------------------------------------------------
# Pre-trade Check  (used by PreTradeAgent)
# ---------------------------------------------------------------------------


class TradeDecision(StrEnum):
    GO = "GO"        # Tín hiệu đồng thuận, có thể vào lệnh
    WAIT = "WAIT"    # Chưa đủ điều kiện, cần chờ thêm
    AVOID = "AVOID"  # Tín hiệu xung đột hoặc rủi ro cao


class AlignmentStatus(StrEnum):
    SUPPORT = "SUPPORT"    # Nguồn này ủng hộ quyết định vào lệnh
    NEUTRAL = "NEUTRAL"    # Nguồn này không có ý kiến rõ ràng
    CONFLICT = "CONFLICT"  # Nguồn này mâu thuẫn với quyết định
    NO_DATA = "NO_DATA"    # Không có dữ liệu từ nguồn này


class ResolutionCategory(StrEnum):
    PRICE = "price"       # Điều kiện về giá / kỹ thuật
    VOLUME = "volume"     # Điều kiện về khối lượng
    NEWS = "news"         # Điều kiện về tin tức / sự kiện
    THESIS = "thesis"     # Điều kiện liên quan đến thesis
    MACRO = "macro"       # Điều kiện vĩ mô / ngành


class ResolutionStep(BaseModel):
    """Một điều kiện cụ thể để chuyển từ WAIT/AVOID → GO.

    Chỉ có nghĩa khi PreTradeCheckOutput.decision != GO.
    AI điền để nhà đầu tư biết đang chờ điều kiện nào,
    không phải chờ vô thời hạn.

    priority:
        1 = bắt buộc — thiếu điều kiện này không thể GO
        2 = nên có   — nếu thiếu vẫn có thể GO nhưng rủi ro cao hơn
        3 = bonus    — có thì tốt, không có cũng được
    """

    condition: str = Field(
        description="Điều kiện cụ thể, có thể đo được. VD: 'VCB giữ trên 85,000 qua 2 phiên'"
    )
    category: ResolutionCategory = Field(
        description="Phân loại điều kiện: price | volume | news | thesis | macro"
    )
    priority: int = Field(
        ge=1, le=3,
        description="1=bắt buộc, 2=nên có, 3=bonus",
    )
    current_status: str = Field(
        description="Trạng thái hiện tại của điều kiện này. VD: 'Hiện tại 82,400 — chưa thỏa'"
    )

    @field_validator("priority", mode="before")
    @classmethod
    def clamp_priority(cls, v: object) -> int:
        try:
            return max(1, min(3, int(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 2


class PreTradeCheckOutput(BaseModel):
    """Structured output from PreTradeAgent.

    Tổng hợp verdict từ nhiều nguồn: thesis, watchlist signal, brief hôm nay.
    decision là kết luận cuối; các *_alignment fields giải thích lý do.

    resolution_path: có giá trị khi decision = WAIT hoặc AVOID.
    Liệt kê từng điều kiện cụ thể cần thỏa để chuyển sang GO,
    theo thứ tự priority (1 trước). Rỗng khi decision = GO.
    """

    ticker: str
    decision: TradeDecision
    confidence: float = Field(ge=0.0, le=1.0, description="Độ tin cậy tổng hợp")
    thesis_alignment: AlignmentStatus = Field(
        description="Thesis hiện tại có ủng hộ quyết định vào lệnh không?"
    )
    signal_alignment: AlignmentStatus = Field(
        description="Watchlist scan signal có đồng thuận không?"
    )
    brief_alignment: AlignmentStatus = Field(
        description="Brief hôm nay đề cập ticker này như thế nào?"
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Mâu thuẫn cụ thể giữa các nguồn dữ liệu",
    )
    conditions: list[str] = Field(
        default_factory=list,
        description="Điều kiện cần thỏa trước khi GO (chỉ có nghĩa khi decision=WAIT)",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Rủi ro cần theo dõi ngay cả khi GO",
    )
    reasoning: str = Field(description="Lý giải tổng hợp của AI về quyết định này")
    resolution_path: list[ResolutionStep] = Field(
        default_factory=list,
        description=(
            "Lộ trình cụ thể để chuyển từ WAIT/AVOID → GO. "
            "Mỗi bước là một điều kiện đo được, có priority và trạng thái hiện tại. "
            "Rỗng khi decision = GO."
        ),
    )

    @field_validator("conflicts", "conditions", "risk_flags", mode="before")
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @field_validator("resolution_path", mode="before")
    @classmethod
    def ensure_resolution_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Thesis Stress-Test  (used by StressTestAgent)
# ---------------------------------------------------------------------------


class ThreatLevel(StrEnum):
    INTACT   = "INTACT"    # Assumption vẫn còn hiệu lực
    WEAKENED = "WEAKENED"  # Đang bị đe dọa nhưng chưa vỡ
    BROKEN   = "BROKEN"    # Assumption đã bị phủ nhận bởi thực tế


class ThreatenedAssumption(BaseModel):
    """Kết quả AI stress-test một assumption cụ thể.

    assumption_id khớp với Assumption.id trong DB (0 nếu assumption
    là free-text không có ID — e.g. từ thesis không có components).
    """

    assumption_id: int = Field(
        default=0,
        description="Assumption.id trong DB. 0 nếu không có ID.",
    )
    description: str = Field(description="Nội dung assumption đang bị test")
    threat_level: ThreatLevel
    evidence: str = Field(
        default="",
        description=(
            "Bằng chứng cụ thể: giá, tin tức, số liệu macro đang mâu thuẫn. "
            "Rỗng nếu AI không cung cấp — xem counter_argument để có context."
        ),
    )
    counter_argument: str = Field(
        description="Counter-argument mạnh nhất AI tìm được để phủ nhận assumption này"
    )


class StressTestOutput(BaseModel):
    """Structured output from StressTestAgent.

    Read-only — StressTestService KHÔNG persist output này.
    Mọi thay đổi thesis phải qua user confirm.
    """

    ticker: str
    thesis_title: str
    verdict: Verdict  # Reuse: BULLISH=thesis còn mạnh, BEARISH=thesis đang vỡ, NEUTRAL=mixed
    invalidation_probability: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Xác suất thesis bị invalidate trong 3-6 tháng tới. "
            "Derived từ tỷ lệ BROKEN + 0.5*WEAKENED assumptions."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    stress_scenario: str = Field(
        description=(
            "Scenario macro AI dùng để stress-test. "
            "VD: 'FED tăng lãi suất thêm 50bps', 'NIM ngân hàng thu hẹp do cạnh tranh'"
        )
    )
    threatened_assumptions: list[ThreatenedAssumption] = Field(
        default_factory=list,
        description="Các assumption bị WEAKENED hoặc BROKEN, theo thứ tự threat_level giảm dần",
    )
    surviving_assumptions: list[str] = Field(
        default_factory=list,
        description="Các assumption vẫn INTACT — lý do thesis chưa bị invalidate hoàn toàn",
    )
    macro_risks: list[str] = Field(
        default_factory=list,
        description="Rủi ro vĩ mô / ngành đang đe dọa thesis, ngoài assumptions cụ thể",
    )
    suggested_triggers_to_watch: list[str] = Field(
        default_factory=list,
        description=(
            "Trigger cụ thể cần theo dõi để biết khi nào thesis thực sự bị invalidate. "
            "VD: 'NIM VCB giảm dưới 3.2% trong Q2 2026'"
        ),
    )
    reasoning: str = Field(description="Lý giải tổng thể của AI về kết quả stress-test")

    @model_validator(mode="before")
    @classmethod
    def normalize_probability_alias(cls, data: object) -> object:
        """Rename trigger_probability → invalidation_probability nếu AI dùng tên cũ."""
        if not isinstance(data, dict):
            return data
        if "invalidation_probability" not in data and "trigger_probability" in data:
            data["invalidation_probability"] = data.pop("trigger_probability")
        return data

    @field_validator("invalidation_probability", "confidence", mode="before")
    @classmethod
    def coerce_probability(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("threatened_assumptions", mode="before")
    @classmethod
    def ensure_threatened_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @field_validator(
        "surviving_assumptions", "macro_risks", "suggested_triggers_to_watch", mode="before"
    )
    @classmethod
    def ensure_str_list(cls, v: object) -> list[object]:
        """Coerce various AI output shapes to list[str]."""
        if isinstance(v, str):
            return [v]
        if isinstance(v, dict):
            result: list[object] = []
            for key, val in v.items():
                if isinstance(val, list):
                    result.extend(f"{key}: {item}" for item in val)
                else:
                    result.append(f"{key}: {val}")
            return result
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Sector Rotation Radar  (used by SectorRotationAgent)
# ---------------------------------------------------------------------------


class FlowDirection(StrEnum):
    INFLOW  = "INFLOW"
    OUTFLOW = "OUTFLOW"
    NEUTRAL = "NEUTRAL"


class RiskRegime(StrEnum):
    RISK_ON  = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    MIXED    = "MIXED"


class SectorFlow(BaseModel):
    """Snapshot dòng tiền của một sector trong phiên / tuần."""

    sector: str = Field(description="Tên sector, VD: Banking, Real Estate")
    avg_change_pct_1d: float = Field(description="% thay đổi trung bình 1 ngày của sector")
    flow_direction: FlowDirection
    top_movers: list[str] = Field(
        default_factory=list,
        description="Top 3 tickers dẫn dắt sector (tăng mạnh nhất hoặc giảm mạnh nhất)",
    )
    ticker_count: int = Field(default=0, description="Số tickers trong sector được theo dõi")


class WatchlistCrosscheck(BaseModel):
    """Một ticker trong watchlist đang đi ngược hoặc cùng dòng sector."""

    ticker: str
    sector: str
    ticker_change_pct: float
    sector_avg_change_pct: float
    is_contrarian: bool = Field(
        description="True nếu ticker đi ngược dòng sector (divergence đáng chú ý)"
    )
    note: str = Field(description="Nhận xét ngắn. VD: 'VCB -1.2% trong khi Banking +0.5%'")


class SectorRotationOutput(BaseModel):
    """Structured output from SectorRotationAgent."""

    snapshot_date: str = Field(description="Ngày snapshot, format YYYY-MM-DD")
    rotation_narrative: str = Field(description="Narrative 2-3 câu mô tả dòng tiền")
    risk_regime: RiskRegime
    leading_sectors: list[SectorFlow] = Field(default_factory=list)
    lagging_sectors: list[SectorFlow] = Field(default_factory=list)
    watchlist_crosscheck: list[WatchlistCrosscheck] = Field(default_factory=list)
    actionable_insight: str = Field(default="")
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("leading_sectors", "lagging_sectors", "watchlist_crosscheck", mode="before")
    @classmethod
    def ensure_sector_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Watchdog  (used by WatchdogAgent)
# ---------------------------------------------------------------------------


class OverallHealth(StrEnum):
    HEALTHY  = "HEALTHY"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


class WatchdogThreatLevel(StrEnum):
    NONE   = "none"
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class WatchdogRecommendedAction(StrEnum):
    HOLD           = "HOLD"
    REVIEW_SOON    = "REVIEW_SOON"
    REVIEW_URGENT  = "REVIEW_URGENT"
    CONSIDER_EXIT  = "CONSIDER_EXIT"


class ThreatenedAssumptionWatchdog(BaseModel):
    """Evaluation of a single assumption threat level."""

    assumption_id: int
    description: str
    threat_level: WatchdogThreatLevel
    threat_reason: str


class WatchdogOutput(BaseModel):
    """Structured output from WatchdogAgent."""

    health_score: int = Field(ge=0, le=100)
    overall_health: OverallHealth
    threatened_assumptions: list[ThreatenedAssumptionWatchdog] = Field(default_factory=list)
    recommended_action: WatchdogRecommendedAction
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("threatened_assumptions", mode="before")
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Decision Replay  (used by ReplayAgent)
# ---------------------------------------------------------------------------


class OutcomeVerdict(StrEnum):
    CORRECT   = "CORRECT"
    INCORRECT = "INCORRECT"
    MIXED     = "MIXED"


class ReplayOutput(BaseModel):
    """Structured output from ReplayAgent."""

    decision_id: int
    ticker: str
    decision_type: str
    outcome_verdict: OutcomeVerdict
    what_went_right: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    key_lesson: str
    pattern_detected: str | None = None
    suggested_adjustment: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("what_went_right", "what_went_wrong", mode="before")
    @classmethod
    def ensure_str_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Signal Credibility  (used by SignalCredibilityAgent)
# ---------------------------------------------------------------------------


class SignalVerdict(StrEnum):
    STRONG   = "STRONG"
    MODERATE = "MODERATE"
    WEAK     = "WEAK"
    NOISE    = "NOISE"


class SignalCredibilityOutput(BaseModel):
    """Structured output from SignalCredibilityAgent."""

    score: int = Field(ge=0, le=100)
    verdict: SignalVerdict
    supporting_factors: list[str] = Field(default_factory=list)
    failure_risks: list[str] = Field(
        default_factory=list,
        description="Ít nhất 2 lý do cụ thể vì sao tín hiệu có thể là false positive",
    )
    volume_confirmed: bool
    trend_aligned: bool
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("supporting_factors", "failure_risks", mode="before")
    @classmethod
    def ensure_str_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Intelligence Loop — Wave 1 schemas
# ---------------------------------------------------------------------------


class SignalUrgency(StrEnum):
    """Urgency level của một ranked signal từ SignalEngine."""

    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


class Signal(BaseModel):
    """Một tín hiệu đã được AI rank và cross-check với thesis + portfolio.

    Là building block của SignalEngineOutput.ranked_signals.
    Downstream: briefing (narrative injection), readmodel (NOW bucket),
    bot (ACT_TODAY card), watchlist (priority update).
    """

    ticker: str
    urgency: SignalUrgency
    signal_type: str = Field(
        description=(
            "Loại tín hiệu. VD: 'thesis_threshold_met', 'technical_breakout', "
            "'thesis_conflict', 'portfolio_concentration', 'catalyst_triggered'"
        )
    )
    headline: str = Field(description="1 câu mô tả tín hiệu, actionable")
    thesis_context: str | None = Field(default=None)
    portfolio_context: str | None = Field(default=None)
    recommended_action: str = Field(description="Hành động đề xuất cụ thể")
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


# ---------------------------------------------------------------------------
# RankedSignal — agent-facing alias of Signal with additional fields
#
# SignalEngineAgent was written against RankedSignal before Signal was
# finalised. RankedSignal extends Signal with the extra fields the agent
# actually populates: verdict, thesis_aligned, trigger_reason, risk_flags,
# action, causal_sources.
#
# Downstream consumers (briefing, readmodel, bot) should treat RankedSignal
# and Signal as interchangeable — RankedSignal is a strict superset.
# ---------------------------------------------------------------------------


class RankedSignal(Signal):
    """Extended Signal with agent-internal fields used by SignalEngineAgent.

    Extra fields vs Signal:
      verdict          — AI verdict (BULLISH/BEARISH/NEUTRAL/WATCHLIST)
      thesis_aligned   — whether signal aligns with active thesis
      trigger_reason   — human-readable trigger description (maps to headline)
      risk_flags       — list of risk strings for this signal
      action           — recommended action string (maps to recommended_action)
      causal_sources   — list of agent/data sources that produced this signal

    Backward-compat: signal_type defaults to 'ranked' when not supplied by
    agent (fallback path uses RankedSignal directly without signal_type).
    """

    signal_type: str = Field(default="ranked")  # override parent to set default
    headline: str = Field(default="")            # override parent — agent uses trigger_reason
    recommended_action: str = Field(default="")  # override parent — agent uses action

    verdict: Verdict = Field(default=Verdict.NEUTRAL)
    thesis_aligned: bool = Field(default=False)
    trigger_reason: str = Field(
        default="",
        description="Human-readable reason this signal was triggered.",
    )
    risk_flags: list[str] = Field(default_factory=list)
    action: str = Field(
        default="",
        description="Recommended action. Mirrors recommended_action for agent compat.",
    )
    causal_sources: list[str] = Field(
        default_factory=list,
        description="Agent/data sources that produced this signal. VD: ['watchdog:VCB']",
    )

    @model_validator(mode="after")
    def sync_alias_fields(self) -> "RankedSignal":
        """Keep headline/recommended_action in sync with trigger_reason/action.

        Agent populates trigger_reason and action; Signal consumers read
        headline and recommended_action. Sync here so both paths work.
        """
        if self.trigger_reason and not self.headline:
            self.headline = self.trigger_reason
        if self.action and not self.recommended_action:
            self.recommended_action = self.action
        return self

    @field_validator("risk_flags", "causal_sources", mode="before")
    @classmethod
    def ensure_str_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PortfolioRiskNote — rule-based portfolio context for SignalEngineAgent
# ---------------------------------------------------------------------------


class PortfolioRiskNote(BaseModel):
    """Rule-based portfolio risk context injected into SignalEngineAgent.

    Built by _build_portfolio_context() in signal_engine.py — not AI-generated.
    Passed to SignalEngineOutput.portfolio_context for downstream consumers.

    Thresholds (rule-based, not AI):
      concentration : weight_pct > 25%
      losing        : pnl_pct < -5%
      misaligned    : holding with last_verdict == BEARISH
    """

    top_concentration: list[str] = Field(
        default_factory=list,
        description="Tickers có weight_pct > 25% — concentration risk",
    )
    losing_positions: list[str] = Field(
        default_factory=list,
        description="Tickers đang lỗ > 5% unrealized",
    )
    misaligned_positions: list[str] = Field(
        default_factory=list,
        description="Tickers đang hold nhưng last_verdict = BEARISH",
    )
    total_pnl_pct: float | None = Field(
        default=None,
        description="Tổng % lãi/lỗ portfolio. None nếu không có portfolio data.",
    )
    position_count: int = Field(default=0)


class RiskAlert(BaseModel):
    """Cảnh báo rủi ro cross-segment từ SignalEngine."""

    alert_type: str = Field(
        description=(
            "Loại rủi ro. VD: 'portfolio_concentration', 'thesis_invalidation_risk', "
            "'macro_regime_shift', 'correlated_exposure'"
        )
    )
    severity: RiskLevel
    description: str = Field(description="Mô tả rủi ro cụ thể, có số liệu nếu có")
    affected_tickers: list[str] = Field(default_factory=list)
    mitigation_hint: str = Field(default="")


class OpportunityHint(BaseModel):
    """Cửa sổ cơ hội ngắn hạn được SignalEngine phát hiện."""

    ticker: str
    opportunity_type: str
    time_sensitivity: str
    rationale: str
    condition: str | None = Field(default=None)


class SignalEngineOutput(BaseModel):
    """Structured output from SignalEngineAgent.

    Owner: ai segment.
    Input: watchlist × thesis × portfolio × market × feedback_history.
    Output feeds: briefing (narrative), watchlist (priority), thesis (review trigger),
                  readmodel (NOW bucket), bot (ACT_TODAY cards).

    Fields added for agent alignment (not AI-generated):
      generated_at      — ISO timestamp of the engine run
      signal_summary    — 1-line summary for bot header (🔴/🟡 format)
      portfolio_context — rule-based PortfolioRiskNote from _build_portfolio_context()
    """

    snapshot_date: str = Field(
        default="",
        description="Ngày chạy signal engine, format YYYY-MM-DD",
    )
    # Agent-populated runtime fields (not from AI structured call)
    generated_at: str = Field(
        default="",
        description="ISO 8601 timestamp khi engine chạy. Set bởi SignalEngineAgent.run().",
    )
    signal_summary: str = Field(
        default="",
        description=(
            "1-line summary cho bot header. "
            "Format: '🔴 <urgent tickers>  🟡 <watch tickers>'. "
            "Set bởi agent sau khi AI call — không do AI điền."
        ),
    )
    portfolio_context: PortfolioRiskNote = Field(
        default_factory=PortfolioRiskNote,
        description=(
            "Rule-based portfolio risk context. "
            "Set bởi _build_portfolio_context() — không do AI điền."
        ),
    )
    ranked_signals: list[RankedSignal] = Field(
        default_factory=list,
        description=(
            "Signals đã rank theo urgency × confidence, từ cao đến thấp. "
            "Max 10 signals — chỉ giữ những gì thực sự actionable."
        ),
    )
    thesis_review_triggers: list[str] = Field(
        default_factory=list,
        description=(
            "Tickers cần ThesisJudgeAgent review ngay. "
            "AI điền khi có mâu thuẫn giữa market data và thesis assumptions."
        ),
    )
    risk_alerts: list[RiskAlert] = Field(
        default_factory=list,
        description="Cờ đỏ cross-segment. Ưu tiên hiển thị trước ranked_signals.",
    )
    opportunity_windows: list[OpportunityHint] = Field(
        default_factory=list,
        description="Cửa sổ cơ hội ngắn hạn được phát hiện qua cross-check.",
    )
    portfolio_concentration_note: str = Field(
        default="",
        description="Nhận xét về concentration risk. Rỗng nếu không có vấn đề.",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Độ tin cậy tổng thể")
    reasoning_summary: str = Field(
        default="",
        description="Tóm tắt ngắn logic của engine run. Dùng cho debug và audit trail.",
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator(
        "ranked_signals", "risk_alerts", "opportunity_windows", "thesis_review_triggers",
        mode="before",
    )
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def build_signal_summary(self) -> "SignalEngineOutput":
        """Derive signal_summary từ ranked_signals nếu agent chưa set.

        Format: '🔴 <CRITICAL/HIGH tickers>  🟡 <MEDIUM tickers>'
        Guard: nếu signal_summary đã được set bởi agent, giữ nguyên.
        """
        if self.signal_summary:
            return self
        urgent = [
            s.ticker for s in self.ranked_signals
            if s.urgency in (SignalUrgency.CRITICAL, SignalUrgency.HIGH)
        ]
        medium = [
            s.ticker for s in self.ranked_signals
            if s.urgency == SignalUrgency.MEDIUM
        ]
        urgent_str = ", ".join(urgent) or "0 urgent"
        medium_str = ", ".join(medium) or "0 watch"
        self.signal_summary = f"🔴 {urgent_str}  🟡 {medium_str}"
        return self

    @model_validator(mode="after")
    def cap_ranked_signals(self) -> "SignalEngineOutput":
        """Giới hạn ranked_signals tối đa 10 items, giữ urgency cao nhất trước."""
        _urgency_order = {
            SignalUrgency.CRITICAL: 0,
            SignalUrgency.HIGH: 1,
            SignalUrgency.MEDIUM: 2,
            SignalUrgency.LOW: 3,
        }
        if len(self.ranked_signals) > 10:
            self.ranked_signals = sorted(
                self.ranked_signals,
                key=lambda s: (_urgency_order.get(s.urgency, 9), -s.confidence),
            )[:10]
        return self


# ---------------------------------------------------------------------------
# Thesis Judge  (used by ThesisJudgeAgent — Wave 1)
# ---------------------------------------------------------------------------


class ThesisConvictionDelta(StrEnum):
    STRENGTHENING = "STRENGTHENING"
    STABLE        = "STABLE"
    WEAKENING     = "WEAKENING"
    INVALIDATING  = "INVALIDATING"


class ThesisJudgeVerdict(StrEnum):
    ON_TRACK    = "ON_TRACK"
    WEAKENING   = "WEAKENING"
    INVALIDATED = "INVALIDATED"
    STRENGTHENING = "STRENGTHENING"


class ChallengedAssumption(BaseModel):
    """Một assumption đang bị thực tế thách thức."""

    assumption_id: int = Field(default=0)
    description: str
    challenge_evidence: str
    severity: Literal["minor", "major", "critical"]


class ThesisJudgeOutput(BaseModel):
    """Structured output from ThesisJudgeAgent.

    Owner: ai segment.
    Triggered by: SignalEngine.thesis_review_triggers hoặc manual /review command.
    Output feeds: briefing (narrative), readmodel (thesis health score),
                  watchlist (alert nếu INVALIDATED), bot (verdict card).
    """

    ticker: str
    thesis_id: str
    verdict: ThesisJudgeVerdict
    conviction_delta: ThesisConvictionDelta
    confidence: float = Field(ge=0.0, le=1.0)
    challenged_assumptions: list[ChallengedAssumption] = Field(default_factory=list)
    new_risks: list[str] = Field(default_factory=list)
    action: Literal["hold", "reduce", "review", "exit_signal"] = Field(default="hold")
    reasoning: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("challenged_assumptions", mode="before")
    @classmethod
    def ensure_challenged_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @field_validator("new_risks", mode="before")
    @classmethod
    def ensure_risks_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
