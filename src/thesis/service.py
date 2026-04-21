"""Thesis service — lifecycle operations for the thesis segment.

Owner: thesis segment.
This is the primary entry point for all thesis write operations.
Bot commands and API routes call this; they do not import models directly.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    RecommendationStatus,
    ReviewRecommendation,
    Thesis,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.thesis.scoring_service import ScoringService
from src.thesis.invalidation_service import InvalidationService
from src.platform.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Timeline parser — converts AI free-form string → datetime
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10,
    "NOVEMBER": 11, "DECEMBER": 12,
}

_Q_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}
_Q_END_DAY   = {3: 31, 6: 30, 9: 30, 12: 31}


def parse_timeline_to_date(timeline: str | None) -> datetime | None:
    """Convert AI free-form timeline string → end-of-period datetime (UTC).

    Supported patterns (case-insensitive):
      "Q3 2026"          → 2026-09-30
      "Q4/2026"          → 2026-12-31
      "H1 2026"          → 2026-06-30
      "H2 2026"          → 2026-12-31
      "tháng 6 2026"     → 2026-06-30
      "06/2026"          → 2026-06-30
      "June 2026"        → 2026-06-30
      "cuối năm 2026"    → 2026-12-31
      "end of 2026"      → 2026-12-31
      "2026"             → 2026-12-31  (fallback)

    Returns None if no pattern matches.
    """
    if not timeline:
        return None
    t = timeline.strip().upper()

    def _eom(year: int, month: int) -> datetime:
        last = calendar.monthrange(year, month)[1]
        return datetime(year, month, last, tzinfo=timezone.utc)

    # Q1-Q4 YYYY  (separator: space, /, -)
    m = re.search(r"Q([1-4])[\s/\-]*(\d{4})", t)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        em = _Q_END_MONTH[q]
        return datetime(y, em, _Q_END_DAY[em], tzinfo=timezone.utc)

    # H1 / H2 YYYY
    m = re.search(r"H([12])[\s/\-]*(\d{4})", t)
    if m:
        h, y = int(m.group(1)), int(m.group(2))
        return _eom(y, 6 if h == 1 else 12)

    # "THÁNG 6 2026" or "THÁNG6/2026"
    m = re.search(r"THÁNG\s*(\d{1,2})[\s/\-]*(\d{4})", t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return _eom(y, mo)

    # "June 2026" / "Jun 2026"
    for name, mo in _MONTH_MAP.items():
        m = re.search(rf"\b{name}\b[\s/\-]*(\d{{4}})", t)
        if m:
            return _eom(int(m.group(1)), mo)

    # "06/2026" or "6-2026"
    m = re.search(r"\b(\d{1,2})[/\-](\d{4})\b", t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return _eom(y, mo)

    # "cuối năm 2026" / "end of year 2026" / "end 2026"
    m = re.search(r"(?:CUỐI\s*NĂM|END\s*OF\s*YEAR?|END)\s*(\d{4})", t)
    if m:
        return datetime(int(m.group(1)), 12, 31, tzinfo=timezone.utc)

    # Bare year fallback: "2026"
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        return datetime(int(m.group(1)), 12, 31, tzinfo=timezone.utc)

    logger.warning("parse_timeline_to_date.unmatched", raw=timeline)
    return None


# ---------------------------------------------------------------------------
# Input DTOs (plain dataclasses — no ORM, safe to cross boundaries)
# ---------------------------------------------------------------------------


@dataclass
class CreateThesisInput:
    user_id: str
    ticker: str
    title: str
    summary: str = ""
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    assumptions: list[str] | None = None
    # CHANGED: accepts AddCatalystInput list so expected_date is preserved.
    # Callers passing list[str] must migrate to AddCatalystInput(description=...).
    catalysts: list[AddCatalystInput] | None = None


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
# Exceptions
# ---------------------------------------------------------------------------


class ThesisNotFoundError(Exception):
    """Raised when a thesis ID does not exist or doesn't belong to the user."""


class ThesisAlreadyClosedError(Exception):
    """Raised when an operation is attempted on a closed/invalidated thesis."""


class AssumptionNotFoundError(Exception):
    """Raised when an assumption does not exist within a thesis."""


class CatalystNotFoundError(Exception):
    """Raised when a catalyst does not exist within a thesis."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ThesisService:
    """Thesis lifecycle: create, update, close, invalidate, delete.
    Also owns assumption and catalyst CRUD within a thesis.

    Caller provides an AsyncSession per-request (from get_db_session()).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ThesisRepository(session)

    # ------------------------------------------------------------------
    # Thesis CRUD
    # ------------------------------------------------------------------

    async def create(self, inp: CreateThesisInput) -> Thesis:
        thesis = Thesis(
            user_id=inp.user_id,
            ticker=inp.ticker.upper(),
            title=inp.title,
            summary=inp.summary,
            status=ThesisStatus.ACTIVE,
            entry_price=inp.entry_price,
            target_price=inp.target_price,
            stop_loss=inp.stop_loss,
        )

        for desc in inp.assumptions or []:
            thesis.assumptions.append(
                Assumption(description=desc, status=AssumptionStatus.PENDING)
            )
        # CHANGED: iterate AddCatalystInput objects, preserve expected_date
        for cat_inp in inp.catalysts or []:
            thesis.catalysts.append(
                Catalyst(
                    description=cat_inp.description,
                    status=cat_inp.status,
                    expected_date=cat_inp.expected_date,
                    note=cat_inp.note,
                )
            )

        await self._repo.save(thesis)
        logger.info("thesis.created", thesis_id=thesis.id, ticker=thesis.ticker)
        return thesis

    async def update(self, thesis_id: int, user_id: str, inp: UpdateThesisInput) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)

        if inp.title is not None:
            thesis.title = inp.title
        if inp.summary is not None:
            thesis.summary = inp.summary
        if inp.entry_price is not None:
            thesis.entry_price = inp.entry_price
        if inp.target_price is not None:
            thesis.target_price = inp.target_price
        if inp.stop_loss is not None:
            thesis.stop_loss = inp.stop_loss

        await self._repo.save(thesis)
        logger.info("thesis.updated", thesis_id=thesis_id)
        return thesis

    async def close(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        thesis.status = ThesisStatus.CLOSED
        thesis.closed_at = datetime.now(timezone.utc)
        await self._repo.save(thesis)
        logger.info("thesis.closed", thesis_id=thesis_id)
        return thesis

    async def invalidate(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        thesis.status = ThesisStatus.INVALIDATED
        thesis.closed_at = datetime.now(timezone.utc)
        await self._repo.save(thesis)
        logger.info("thesis.invalidated", thesis_id=thesis_id)
        return thesis

    async def delete(self, thesis_id: int, user_id: str) -> None:
        """Hard delete a thesis and all its children (cascade)."""
        thesis = await self._get_owned(thesis_id, user_id)
        await self._repo.delete(thesis)
        logger.info("thesis.deleted", thesis_id=thesis_id)

    async def get(self, thesis_id: int, user_id: str) -> Thesis:
        return await self._get_owned(thesis_id, user_id)

    async def list_for_user(
        self,
        user_id: str,
        status: ThesisStatus | None = None,
    ) -> list[Thesis]:
        return await self._repo.list_by_user(user_id, status)

    async def _recompute_score(self, thesis_id: int) -> None:
        """Reload thesis (with relationships) và persist lại score mới."""
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            return
        new_score = ScoringService().compute(thesis)
        if thesis.score != new_score:
            thesis.score = new_score
            await self._repo.save(thesis)
            logger.info("thesis.score_recomputed", thesis_id=thesis_id, score=new_score)

    async def _auto_invalidate_if_needed(self, thesis_id: int) -> None:
        """Sau khi score recompute, kiểm tra invalidation conditions.
    
        Nếu InvalidationService.check() trả should_invalidate=True
        → tự chuyển thesis sang INVALIDATED trong cùng transaction.
        Không raise — failure ở đây không nên block caller.
        """
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            return
        if thesis.status != ThesisStatus.ACTIVE:
            return  # chỉ check thesis đang active
    
        current_score = thesis.score or 0.0
        result = InvalidationService().check(thesis, current_score)
    
        if result.should_invalidate:
            thesis.status = ThesisStatus.INVALIDATED
            thesis.closed_at = datetime.now(timezone.utc)
            await self._repo.save(thesis)
            logger.warning(
                "thesis.auto_invalidated",
                thesis_id=thesis_id,
                reason=result.reason,
                invalid_assumptions=result.invalid_assumptions,
                score=result.score,
            )
    
    # ------------------------------------------------------------------
    # Assumption CRUD
    # ------------------------------------------------------------------

    async def add_assumption(
        self, thesis_id: int, user_id: str, inp: AddAssumptionInput
    ) -> Assumption:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)

        assumption = Assumption(
            thesis_id=thesis_id,
            description=inp.description,
            status=inp.status,
            note=inp.note,
        )
        await self._repo.save_assumption(assumption)
        logger.info("assumption.added", thesis_id=thesis_id, assumption_id=assumption.id)
        return assumption

    async def update_assumption(
        self,
        thesis_id: int,
        assumption_id: int,
        user_id: str,
        inp: UpdateAssumptionInput,
    ) -> Assumption:
        await self._get_owned(thesis_id, user_id)
        assumption = await self._repo.get_assumption_by_id(assumption_id, thesis_id)
        if assumption is None:
            raise AssumptionNotFoundError(
                f"Assumption {assumption_id} not found in thesis {thesis_id}"
            )

        if inp.description is not None:
            assumption.description = inp.description
        if inp.status is not None:
            assumption.status = inp.status
        if inp.note is not None:
            assumption.note = inp.note

        await self._repo.save_assumption(assumption)
        logger.info("assumption.updated", assumption_id=assumption_id)
        await self._recompute_score(thesis_id)
        await self._auto_invalidate_if_needed(thesis_id)  # Wave 3
        return assumption

    async def delete_assumption(
        self, thesis_id: int, assumption_id: int, user_id: str
    ) -> None:
        await self._get_owned(thesis_id, user_id)
        assumption = await self._repo.get_assumption_by_id(assumption_id, thesis_id)
        if assumption is None:
            raise AssumptionNotFoundError(
                f"Assumption {assumption_id} not found in thesis {thesis_id}"
            )
        await self._repo.delete_assumption(assumption)
        logger.info("assumption.deleted", assumption_id=assumption_id, thesis_id=thesis_id)
        await self._recompute_score(thesis_id)

    # ------------------------------------------------------------------
    # Catalyst CRUD
    # ------------------------------------------------------------------

    async def add_catalyst(
        self, thesis_id: int, user_id: str, inp: AddCatalystInput
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)

        catalyst = Catalyst(
            thesis_id=thesis_id,
            description=inp.description,
            status=inp.status,
            expected_date=inp.expected_date,
            note=inp.note,
        )
        await self._repo.save_catalyst(catalyst)
        logger.info("catalyst.added", thesis_id=thesis_id, catalyst_id=catalyst.id)
        return catalyst

    async def update_catalyst(
        self,
        thesis_id: int,
        catalyst_id: int,
        user_id: str,
        inp: UpdateCatalystInput,
    ) -> Catalyst:
        await self._get_owned(thesis_id, user_id)
        catalyst = await self._repo.get_catalyst_by_id(catalyst_id, thesis_id)
        if catalyst is None:
            raise CatalystNotFoundError(
                f"Catalyst {catalyst_id} not found in thesis {thesis_id}"
            )

        if inp.description is not None:
            catalyst.description = inp.description
        if inp.status is not None:
            catalyst.status = inp.status
        if inp.expected_date is not None:
            catalyst.expected_date = inp.expected_date
        if inp.triggered_at is not None:
            catalyst.triggered_at = inp.triggered_at
        if inp.note is not None:
            catalyst.note = inp.note

        await self._repo.save_catalyst(catalyst)
        logger.info("catalyst.updated", catalyst_id=catalyst_id)
        await self._recompute_score(thesis_id)
        return catalyst

    async def delete_catalyst(
        self, thesis_id: int, catalyst_id: int, user_id: str
    ) -> None:
        await self._get_owned(thesis_id, user_id)
        catalyst = await self._repo.get_catalyst_by_id(catalyst_id, thesis_id)
        if catalyst is None:
            raise CatalystNotFoundError(
                f"Catalyst {catalyst_id} not found in thesis {thesis_id}"
            )
        await self._repo.delete_catalyst(catalyst)
        logger.info("catalyst.deleted", catalyst_id=catalyst_id, thesis_id=thesis_id)
        await self._recompute_score(thesis_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None or thesis.user_id != user_id:
            raise ThesisNotFoundError(f"Thesis {thesis_id} not found for user {user_id}")
        return thesis

    @staticmethod
    def _assert_mutable(thesis: Thesis) -> None:
        if thesis.status in (ThesisStatus.CLOSED, ThesisStatus.INVALIDATED):
            raise ThesisAlreadyClosedError(
                f"Thesis {thesis.id} is already {thesis.status} and cannot be modified."
            )

    async def apply_recommendation(
        self,
        thesis_id: int,
        recommendation_id: int,
        user_id: str,
        accept: bool,
    ) -> None:
        """Apply hoặc reject một AI recommendation.

        - accept=True  → apply status change lên assumption/catalyst, mark ACCEPTED
        - accept=False → mark REJECTED, không thay đổi gì khác
        acted_at được set trong cả 2 nhánh để đảm bảo audit trail đầy đủ.
        """
        await self._get_owned(thesis_id, user_id)
        rec = await self._repo.get_recommendation_by_id(recommendation_id)
        if rec is None:
            raise ValueError(f"Recommendation {recommendation_id} not found")

        if rec.review.thesis_id != thesis_id:
            raise ValueError(
                f"Recommendation {recommendation_id} does not belong to thesis {thesis_id}"
            )
        
        now = datetime.now(timezone.utc)

        if not accept:
            rec.status = RecommendationStatus.REJECTED
            rec.acted_at = now                          # 👈 fix bug 2
            await self._repo.save_recommendation(rec)
            logger.info(
                "recommendation.rejected",
                recommendation_id=recommendation_id,
                thesis_id=thesis_id,
            )
            return

        if rec.target_type == "assumption":
            inp = UpdateAssumptionInput(status=AssumptionStatus(rec.recommended_status))
            await self.update_assumption(thesis_id, rec.target_id, user_id, inp)
        elif rec.target_type == "catalyst":
            inp = UpdateCatalystInput(status=CatalystStatus(rec.recommended_status))
            await self.update_catalyst(thesis_id, rec.target_id, user_id, inp)

        rec.status = RecommendationStatus.ACCEPTED
        rec.acted_at = now                              # 👈 fix bug 2
        await self._repo.save_recommendation(rec)
        logger.info(
            "recommendation.accepted",
            recommendation_id=recommendation_id,
            target_type=rec.target_type,
            target_id=rec.target_id,
            recommended_status=rec.recommended_status,
            thesis_id=thesis_id,
        )
