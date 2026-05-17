"""
AI Agents — Public API

All agent facades exposed here. Callers outside this segment
import from `src.ai.agents`, not from individual modules.
"""
from .briefing import BriefingAgent
from .invalidation_detector import ThesisInvalidationDetector
from .next_action_suggester import NextActionSuggester
from .portfolio_risk_narrator import PortfolioRiskNarratorAgent, PortfolioRiskNarratorContext
from .pretrade import PreTradeAgent
from .proactive_alert_agent import ProactiveAlertAgent, get_proactive_alert_agent
from .replay import ReplayAgent
from .sector_rotation import SectorRotationAgent
from .signal_credibility import SignalCredibilityAgent
from .signal_engine import SignalEngineAgent
from .stress_test import StressTestAgent
from .suggest_agent import ThesisSuggestAgent
from .thesis_judge import ThesisJudgeAgent
from .thesis_review import ThesisReviewAgent
from .watchdog import WatchdogAgent

__all__ = [
    "BriefingAgent",
    "NextActionSuggester",
    "PortfolioRiskNarratorAgent",
    "PortfolioRiskNarratorContext",
    "PreTradeAgent",
    "ProactiveAlertAgent",
    "get_proactive_alert_agent",
    "ReplayAgent",
    "SectorRotationAgent",
    "SignalCredibilityAgent",
    "SignalEngineAgent",
    "StressTestAgent",
    "ThesisSuggestAgent",
    "ThesisInvalidationDetector",
    "ThesisJudgeAgent",
    "ThesisReviewAgent",
    "WatchdogAgent",
]
