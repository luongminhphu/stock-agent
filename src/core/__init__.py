"""
Core Intelligence Engine — cross-segment orchestration layer.

Owner: core segment.
Public API:
    IntelligenceEngineListener  — register with EventBus in bootstrap
"""
from src.core.intelligence_listener import IntelligenceEngineListener

__all__ = ["IntelligenceEngineListener"]
