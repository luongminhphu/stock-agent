"""Thesis segment — Input DTOs and domain exceptions.

Owner: thesis segment.
Plain dataclasses only — no ORM, no SQLAlchemy, safe to cross segment boundaries.
Import từ đây thay vì từ service.py để tránh circular deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.thesis.models import AssumptionStatus, CatalystStatus


# ---------------------------------------------------------------------------
# Input DTOs
# ---------------------------------------------------------------------------


@dataclass
class CreateThesisInput:
    ticker: str
    title: str
    summary: str = ""
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    assumptions: list[str] | None = None
    catalysts: list[AddCatalystInput] | None = None
    user_id: str | None = None


@dataclass
class UpdateThesisInput:
    title: str | None = None
    summary: str | None = None
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None


@dataclass
class AddAssumptionInput:
    description: str
    status: AssumptionStatus = AssumptionStatus.PENDING
    note: str | None = None


@dataclass
class UpdateAssumptionInput:
    description: str | None = None
    status: AssumptionStatus | None = None
    note: str | None = None


@dataclass
class AddCatalystInput:
    description: str
    status: CatalystStatus = CatalystStatus.PENDING
    expected_date: datetime | None = None
    note: str | None = None


@dataclass
class UpdateCatalystInput:
    description: str | None = None
    status: CatalystStatus | None = None
    expected_date: datetime | None = None
    triggered_at: datetime | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ThesisNotFoundError(Exception):
    """Raised when a thesis ID does not exist or doesn't belong to the user."""


class ThesisAlreadyClosedError(Exception):
    """Raised when an operation is attempted on a closed/invalidated thesis."""


class AssumptionNotFoundError(Exception):
    """Raised when an assumption does not exist within a thesis."""


class CatalystNotFoundError(Exception):
    """Raised when a catalyst does not exist within a thesis."""
