"""Thesis segment — Input DTOs and domain exceptions.

Owner: thesis segment.
Plain dataclasses only — no ORM, no SQLAlchemy, safe to cross segment boundaries.
Import từ đây thay vì từ service.py để tránh circular deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.thesis.models import AssumptionStatus, CatalystStatus, ThesisDirection

# ---------------------------------------------------------------------------
# Input DTOs
# ---------------------------------------------------------------------------


@dataclass
class CreateThesisInput:
    ticker: str
    title: str
    summary: str = ""
    direction: ThesisDirection | None = None
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    time_horizon: str | None = None  # e.g. "Q3/2026", "6 tháng" — parsed to target_date by service
    assumptions: list[str] | None = None
    catalysts: list[AddCatalystInput | str] | None = None  # str -> AddCatalystInput(description)
    user_id: str | None = None


@dataclass
class UpdateThesisInput:
    title: str | None = None
    summary: str | None = None
    direction: ThesisDirection | None = None
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    time_horizon: str | None = None  # e.g. "Q3/2026", "6 tháng" — parsed to target_date by service


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
    timeline: str | None = None  # AI-generated timeline string, e.g. "Q3/2026", "tháng 6/2026"
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
    """Raised when an operation is attempted on a closed/invalidated/paused thesis.

    Message là tiếng Việt đọc được — api trả thẳng làm `detail` (trước đây chỉ là id).
    """

    def __init__(self, thesis_id: int, status: str | None = None) -> None:
        self.thesis_id = thesis_id
        self.status = status
        state = f"trạng thái '{status}'" if status else "trạng thái đã đóng"
        super().__init__(
            f"Thesis #{thesis_id} đang ở {state} — mở lại thesis trước khi sửa hoặc đổi trạng thái"
        )


class ThesisConflictError(Exception):
    """Raised when a lifecycle change would violate an invariant (vd: 2 thesis active cùng mã)."""


class AssumptionNotFoundError(Exception):
    """Raised when an assumption does not exist within a thesis."""


class CatalystNotFoundError(Exception):
    """Raised when a catalyst does not exist within a thesis."""


class RecommendationNotFoundError(Exception):
    """Raised when a recommendation does not exist."""
