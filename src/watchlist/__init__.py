"""Watchlist segment — watchlist CRUD, alerts, scan service, reminders.

Public API:
    Models:      WatchlistItem, Alert, Reminder
    Enums:       AlertConditionType, AlertStatus, ReminderFrequency
    Repository:  WatchlistRepository
    Services:    WatchlistService, ScanService
    DTOs:        AddToWatchlistInput, CreateAlertInput
    Results:     ScanResult, ScanSignal
    Errors:      WatchlistItemNotFoundError, WatchlistItemAlreadyExistsError,
                 AlertNotFoundError, ScanServiceNotConfiguredError

    --- Blueprint V2: Signal Engine ---
    SignalEngine, SignalReport, SignalType

    --- Wave 2: Review Outcome Reactor ---
    ReviewOutcomeReactor
"""

from src.watchlist.models import (
    Alert,
    AlertConditionType,
    AlertStatus,
    Reminder,
    ReminderFrequency,
    WatchlistItem,
)
from src.watchlist.repository import WatchlistRepository
from src.watchlist.review_outcome_reactor import ReviewOutcomeReactor
from src.watchlist.scan_service import (
    ScanResult,
    ScanService,
    ScanServiceNotConfiguredError,
    ScanSignal,
)
from src.watchlist.service import (
    AddToWatchlistInput,
    AlertNotFoundError,
    CreateAlertInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)
from src.watchlist.signal_engine import SignalEngine, SignalReport, SignalType

__all__ = [
    # Models
    "WatchlistItem",
    "Alert",
    "Reminder",
    # Enums
    "AlertConditionType",
    "AlertStatus",
    "ReminderFrequency",
    # Repository
    "WatchlistRepository",
    # Services
    "WatchlistService",
    "ScanService",
    # DTOs
    "AddToWatchlistInput",
    "CreateAlertInput",
    # Results
    "ScanResult",
    "ScanSignal",
    # Errors
    "WatchlistItemNotFoundError",
    "WatchlistItemAlreadyExistsError",
    "AlertNotFoundError",
    "ScanServiceNotConfiguredError",
    # V2: Signal Engine
    "SignalEngine",
    "SignalReport",
    "SignalType",
    # Wave 2: Review Outcome Reactor
    "ReviewOutcomeReactor",
]
