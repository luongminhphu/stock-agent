"""Platform segment — infra-level concerns only.

Public API:
    settings              — singleton Settings instance
    get_settings()        — factory (same singleton)
    Base                  — SQLAlchemy DeclarativeBase for all models
    AsyncSessionLocal     — session factory
    get_db_session()      — async generator for FastAPI DI / context managers
    configure_logging()   — call once at startup
    get_logger()          — get a named structlog logger
    bootstrap()           — async startup routine (logging + future: migrations)
    check_health()        — async health probe

    --- Event Bus ---
    get_event_bus()       — return the global EventBus singleton
    reset_event_bus()     — reset singleton (tests only)
    EventBus              — the bus class (for type hints)
    DomainEvent           — base event class
    All typed events from src.platform.events (30 event classes)
"""

from src.platform.bootstrap import bootstrap
from src.platform.config import get_settings, settings
from src.platform.db import AsyncSessionLocal, Base, get_db_session
from src.platform.event_bus import EventBus, get_event_bus, reset_event_bus
from src.platform.events import (
    BriefingReadyEvent,
    BriefingRequestedEvent,
    DailyAgendaCompletedEvent,
    DomainEvent,
    EngineFeedbackSubmittedEvent,
    EvolutionSuggestionReadyEvent,
    IntelligenceEngineCompletedEvent,
    IntelligenceEngineRequestedEvent,
    OpportunityAIAnalysisRequestedEvent,
    OpportunityAnalysisCompletedEvent,
    OpportunityScreenCompletedEvent,
    PortfolioSnapshotReadyEvent,
    PortfolioSnapshotRequestedEvent,
    PositionRiskBreachedEvent,
    ProactiveDiscoveryReadyEvent,
    ProactiveWatchAlertFiredEvent,
    ProactiveWatchRequestedEvent,
    RecommendationReadyEvent,
    SignalDetectedEvent,
    SignalEngineCompletedEvent,
    SignalEngineRequestedEvent,
    StressTestCompletedEvent,
    ThesisClosedEvent,
    ThesisInvalidatedEvent,
    ThesisPostMortemReadyEvent,
    ThesisReviewRequestedEvent,
    ThesisReviewTriggeredEvent,
    TrendPredictionCompletedEvent,
    TrendShiftEvent,
    UserActionEvent,
    WatchlistScanCompletedEvent,
)
from src.platform.health import HealthReport, HealthStatus, check_health
from src.platform.logging import configure_logging, get_logger

__all__ = [
    # infra
    "bootstrap",
    "get_settings",
    "settings",
    "AsyncSessionLocal",
    "Base",
    "get_db_session",
    "HealthReport",
    "HealthStatus",
    "check_health",
    "configure_logging",
    "get_logger",
    # event bus
    "EventBus",
    "get_event_bus",
    "reset_event_bus",
    # events
    "DomainEvent",
    "BriefingReadyEvent",
    "BriefingRequestedEvent",
    "DailyAgendaCompletedEvent",
    "EngineFeedbackSubmittedEvent",
    "EvolutionSuggestionReadyEvent",
    "IntelligenceEngineCompletedEvent",
    "IntelligenceEngineRequestedEvent",
    "OpportunityAIAnalysisRequestedEvent",
    "OpportunityAnalysisCompletedEvent",
    "OpportunityScreenCompletedEvent",
    "PortfolioSnapshotReadyEvent",
    "PortfolioSnapshotRequestedEvent",
    "PositionRiskBreachedEvent",
    "ProactiveDiscoveryReadyEvent",
    "ProactiveWatchAlertFiredEvent",
    "ProactiveWatchRequestedEvent",
    "RecommendationReadyEvent",
    "SignalDetectedEvent",
    "SignalEngineCompletedEvent",
    "SignalEngineRequestedEvent",
    "StressTestCompletedEvent",
    "ThesisClosedEvent",
    "ThesisInvalidatedEvent",
    "ThesisPostMortemReadyEvent",
    "ThesisReviewRequestedEvent",
    "ThesisReviewTriggeredEvent",
    "TrendPredictionCompletedEvent",
    "TrendShiftEvent",
    "UserActionEvent",
    "WatchlistScanCompletedEvent",
]
