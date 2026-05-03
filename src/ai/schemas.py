"""Structured output schemas for all AI agents.

These Pydantic models define the contract between the AI layer and
calling segments. All structured responses from Perplexity must
parse into one of these schemas.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

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

    @field_validator("risk_signals", "next_watch_items", mode="before")
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        return v  # type: ignore[return-value]

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
    action_items: list[str] = Field(default_factory=list, description="Suggested actions to review")
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
