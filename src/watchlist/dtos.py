"""Watchlist segment — Input DTOs and domain exceptions.

Owner: watchlist segment.
Plain dataclasses only — no ORM, no SQLAlchemy, safe to cross segment boundaries.
Import từ đây thay vì từ service.py để tránh circular deps.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.watchlist.models import AlertConditionType


# ---------------------------------------------------------------------------
# Input DTOs
# ---------------------------------------------------------------------------


@dataclass
class AddToWatchlistInput:
    user_id: str
    ticker: str
    note: str = ""
    thesis_id: int | None = None
    priority: int = 100


@dataclass
class CreateAlertInput:
    user_id: str
    ticker: str
    condition_type: AlertConditionType
    threshold: float
    note: str = ""
    watchlist_item_id: int | None = None


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class WatchlistItemNotFoundError(Exception):
    """Raised when a ticker is not found in the user's watchlist."""


class WatchlistItemAlreadyExistsError(Exception):
    """Raised when trying to add a ticker already in the watchlist."""


class AlertNotFoundError(Exception):
    """Raised when an alert ID does not exist for the user."""
