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
from src.watchlist.scan_service import (
    ScanResult,
    ScanServiceNotConfiguredError,
    ScanSignal,
    ScanService,
)
from src.watchlist.service import (
    AddToWatchlistInput,
    AlertNotFoundError,
    CreateAlertInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)

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
]
