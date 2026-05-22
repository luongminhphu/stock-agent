"""
src/ai/schemas/__init__.py

Full re-export of all AI agent schemas.

Every ``from src.ai.schemas import X`` that worked against the old
monolithic schemas.py continues to work unchanged — zero caller migrations.

Add new schemas in the appropriate sub-file, then add the import +
__all__ entry here.
"""

# --- Base ---
from src.ai.schemas._base import RiskLevel, Verdict, _coerce_confidence

# --- Thesis Review ---
from src.ai.schemas.thesis_review import (
    AssumptionRecommendation,
    CatalystRecommendation,
    ThesisReviewOutput,
)

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

# --- Stock Analysis ---
from src.ai.schemas.stock_analysis import StockAnalysisOutput

# --- Proactive Alert ---
from src.ai.schemas.proactive_alert import ProactiveAlertOutput, RiskSignal

# --- Thesis Suggestion ---
from src.ai.schemas.thesis_suggestion import (
    SuggestedAssumption,
    SuggestedCatalyst,
    ThesisSuggestionResult,
)

# --- Why ---
from src.ai.schemas.why import MovementDirection, WhyOutput

# --- Pre-Trade ---
from src.ai.schemas.pretrade import (
    AlignmentStatus,
    PreTradeCheckOutput,
    ResolutionCategory,
    ResolutionStep,
    TradeDecision,
)

# --- Stress Test ---
from src.ai.schemas.stress_test import (
    StressTestOutput,
    ThreatLevel,
    ThreatenedAssumption,
)

# --- Sector Rotation ---
from src.ai.schemas.sector_rotation import (
    FlowDirection,
    RiskRegime,
    SectorFlow,
    SectorRotationOutput,
    WatchlistCrosscheck,
)

# --- Watchdog ---
from src.ai.schemas.watchdog import (
    OverallHealth,
    ThreatenedAssumptionWatchdog,
    WatchdogOutput,
    WatchdogRecommendedAction,
    WatchdogThreatLevel,
)

# --- Replay ---
from src.ai.schemas.replay import OutcomeVerdict, ReplayOutput

# --- Signal Credibility ---
from src.ai.schemas.signal_credibility import (
    SignalCredibilityOutput,
    SignalVerdict,
)

# --- Signal Engine ---
from src.ai.schemas.signal_engine import (
    OpportunityHint,
    PortfolioRiskNote,
    RankedSignal,
    RiskAlert,
    Signal,
    SignalEngineOutput,
    SignalUrgency,
)

# --- Thesis Judge ---
from src.ai.schemas.thesis_judge import (
    ChallengedAssumption,
    ThesisConvictionDelta,
    ThesisJudgeOutput,
    ThesisJudgeVerdict,
)

# --- Portfolio Risk Narrator ---
from src.ai.schemas.portfolio_risk import (
    PortfolioRiskNarrativeOutput,
    RiskChapter,
    RiskTheme,
)

# --- Thesis Invalidation ---
from src.ai.schemas.invalidation import (
    BreachType,
    InvalidationSignal,
    InvalidationVerdict,
)

# --- Next Action ---
from src.ai.schemas.next_action import (
    ActionScope,
    NextActionPlan,
    SuggestedAction,
)

# --- Post Mortem ---
from src.ai.schemas.post_mortem import PostMortemOutput, PostMortemVerdict

# --- Thesis Debate ---
from src.ai.schemas.thesis_debate import (
    ChallengeStrength,
    DebateChallenge,
    DebateOutput,
    OverallStance,
)

__all__ = [
    # Base
    "Verdict",
    "RiskLevel",
    "_coerce_confidence",
    # Thesis Review
    "AssumptionRecommendation",
    "CatalystRecommendation",
    "ThesisReviewOutput",
    # Briefing
    "MarketSentiment",
    "ActionPriority",
    "PrioritizedAction",
    "ActionQueue",
    "WatchlistTickerSummary",
    "PortfolioPositionBrief",
    "BriefOutput",
    # Stock Analysis
    "StockAnalysisOutput",
    # Proactive Alert
    "RiskSignal",
    "ProactiveAlertOutput",
    # Thesis Suggestion
    "SuggestedAssumption",
    "SuggestedCatalyst",
    "ThesisSuggestionResult",
    # Why
    "MovementDirection",
    "WhyOutput",
    # Pre-Trade
    "TradeDecision",
    "AlignmentStatus",
    "ResolutionCategory",
    "ResolutionStep",
    "PreTradeCheckOutput",
    # Stress Test
    "ThreatLevel",
    "ThreatenedAssumption",
    "StressTestOutput",
    # Sector Rotation
    "FlowDirection",
    "RiskRegime",
    "SectorFlow",
    "WatchlistCrosscheck",
    "SectorRotationOutput",
    # Watchdog
    "OverallHealth",
    "WatchdogThreatLevel",
    "WatchdogRecommendedAction",
    "ThreatenedAssumptionWatchdog",
    "WatchdogOutput",
    # Replay
    "OutcomeVerdict",
    "ReplayOutput",
    # Signal Credibility
    "SignalVerdict",
    "SignalCredibilityOutput",
    # Signal Engine
    "SignalUrgency",
    "Signal",
    "RankedSignal",
    "PortfolioRiskNote",
    "RiskAlert",
    "OpportunityHint",
    "SignalEngineOutput",
    # Thesis Judge
    "ThesisConvictionDelta",
    "ThesisJudgeVerdict",
    "ChallengedAssumption",
    "ThesisJudgeOutput",
    # Portfolio Risk Narrator
    "RiskTheme",
    "RiskChapter",
    "PortfolioRiskNarrativeOutput",
    # Thesis Invalidation
    "BreachType",
    "InvalidationVerdict",
    "InvalidationSignal",
    # Next Action
    "ActionScope",
    "SuggestedAction",
    "NextActionPlan",
    # Post Mortem
    "PostMortemVerdict",
    "PostMortemOutput",
    # Thesis Debate
    "OverallStance",
    "ChallengeStrength",
    "DebateChallenge",
    "DebateOutput",
]
