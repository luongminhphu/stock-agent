"""
AI Agents — Public API

All agent facades exposed here. Callers outside this segment
import from `src.ai.agents`, not from individual modules.
"""
from .briefing import BriefingAgent
from .pretrade import PreTradeAgent
from .proactive_alert_agent import ProactiveAlertAgent, get_proactive_alert_agent
from .replay import ReplayAgent
from .signal_credibility import SignalCredibilityAgent
from .suggest_agent import ThesisSuggestAgent
from .thesis_review import ThesisReviewAgent
from .watchdog import WatchdogAgent

__all__ = [
    "BriefingAgent",
    "PreTradeAgent",
    "ProactiveAlertAgent",
    "get_proactive_alert_agent",
    "ReplayAgent",
    "SignalCredibilityAgent",
    "ThesisSuggestAgent",
    "ThesisReviewAgent",
    "WatchdogAgent",
]
