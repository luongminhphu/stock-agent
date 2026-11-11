"""
src/ai/schemas/__init__.py

Full re-export of all AI agent schemas.

Every ``from src.ai.schemas import X`` that worked against the old
monolithic schemas.py continues to work unchanged — zero caller migrations.

Add new schemas in the appropriate sub-file, then add the import +
__all__ entry here.
"""

# --- Base ---
# --- Ranked signals / risk alerts (input tuỳ chọn của narrator/briefing) ---
from src.ai.schemas._base import PortfolioRiskNote, RiskLevel, Verdict, _coerce_confidence

# --- Briefing ---
from src.ai.schemas.briefing import (
    ActionPriority,
    ActionQueue,
    BriefOutput,
    MarketSentiment,
    PortfolioPositionBrief,
    PrioritizedAction,
    WatchlistTickerSummary,
)

# --- Portfolio Risk Narrator ---
from src.ai.schemas.portfolio_risk import (
    PortfolioRiskNarrativeOutput,
    RiskChapter,
    RiskTheme,
)
from src.ai.schemas.portfolio_signals import RankedSignal, RiskAlert, Signal, SignalUrgency

# --- Post Mortem ---
from src.ai.schemas.post_mortem import PostMortemOutput, PostMortemVerdict

# --- Pre-Trade ---
from src.ai.schemas.pretrade import (
    AlignmentStatus,
    PreTradeCheckOutput,
    ResolutionCategory,
    ResolutionStep,
    TradeDecision,
)

# --- Replay ---
from src.ai.schemas.replay import OutcomeVerdict, ReplayOutput

# --- Stock Analysis ---
from src.ai.schemas.stock_analysis import StockAnalysisOutput

# --- Thesis Review ---
from src.ai.schemas.thesis_review import (
    AssumptionRecommendation,
    CatalystRecommendation,
    ThesisReviewOutput,
)

# --- Thesis Suggestion ---
from src.ai.schemas.thesis_suggestion import (
    SuggestedAssumption,
    SuggestedCatalyst,
    ThesisSuggestionResult,
)

__all__ = [
    "Verdict",
    "RiskLevel",
    "_coerce_confidence",
    "AssumptionRecommendation",
    "CatalystRecommendation",
    "ThesisReviewOutput",
    "MarketSentiment",
    "ActionPriority",
    "PrioritizedAction",
    "ActionQueue",
    "WatchlistTickerSummary",
    "PortfolioPositionBrief",
    "BriefOutput",
    "StockAnalysisOutput",
    "SuggestedAssumption",
    "SuggestedCatalyst",
    "ThesisSuggestionResult",
    "TradeDecision",
    "AlignmentStatus",
    "ResolutionCategory",
    "ResolutionStep",
    "PreTradeCheckOutput",
    "OutcomeVerdict",
    "ReplayOutput",
    "SignalUrgency",
    "Signal",
    "RankedSignal",
    "PortfolioRiskNote",
    "RiskAlert",
    "RiskTheme",
    "RiskChapter",
    "PortfolioRiskNarrativeOutput",
    "PostMortemVerdict",
    "PostMortemOutput",
]
