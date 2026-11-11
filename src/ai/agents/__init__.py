"""
AI Agents — Public API

All agent facades exposed here. Callers outside this segment
import from `src.ai.agents`, not from individual modules.
"""

from .agenda_builder import AgendaBuilderAgent
from .briefing import BriefingAgent
from .invalidation_detector import ThesisInvalidationDetector
from .next_action_suggester import NextActionSuggester
from .portfolio_risk_narrator import PortfolioRiskNarratorAgent, PortfolioRiskNarratorContext
from .pretrade import PreTradeAgent
from .replay import ReplayAgent
from .suggest_agent import ThesisSuggestAgent
from .thesis_review import ThesisReviewAgent
from .watchdog import WatchdogAgent

__all__ = [
    "AgendaBuilderAgent",
    "BriefingAgent",
    "NextActionSuggester",
    "PortfolioRiskNarratorAgent",
    "PortfolioRiskNarratorContext",
    "PreTradeAgent",
    "ReplayAgent",
    "ThesisSuggestAgent",
    "ThesisInvalidationDetector",
    "ThesisReviewAgent",
    "WatchdogAgent",
]
