"""Thesis component services — Assumption, Catalyst, Recommendation.

Owner: thesis segment.
Chịu trách nhiệm toàn bộ CRUD cho assumption, catalyst và apply_recommendation.
Gọi ScoringService + InvalidationService sau mỗi mutation để giữ score nhất quán.

Không chứa thesis lifecycle logic (create/close/invalidate) — xem service.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.config import settings
from src.platform.logging import get_logger
from src.thesis.dtos import (
    AddAssumptionInput,
    AddCatalystInput,
    UpdateAssumptionInput,
    UpdateCatalystInput,
)
from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    RecommendationStatus,
    Thesis,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.thesis.scoring_service import ScoringService
from src.thesis.invalidation_service import InvalidationService
from src.thesis.timeline_parser import parse_timeline_to_date

logger = get_logger(__name__)


def _resolve_user_id(user_id: str | None) -> str:
    resolved = user_id or settings.owner_user_id
    if not resolved:
        raise ValueError(
            "user_id is required. Set owner_user_id in settings/.env for single-user mode."
        )
    return resolved


class ComponentService:
    """CRUD cho Assumption, Catalyst và apply_recommendation.

    Caller cung cấp AsyncSession per-request.
    Không gọi trực tiếp từ bot — dùng qua ThesisService facade.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ThesisRepository(session)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _recompute_score(self, thesis_id: int) -> None:
        """Reload thesis và persist score mới nếu thay đổi."""
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            return
        new_score = ScoringService().compute(thesis)
        if thesis.score != new_score:
            thesis.score = new_score
            await self._repo.save(thesis)
            logger.info("thesis.score_recomputed", thesis_id=thesis_id, score=new_score)

    async def _auto_invalidate_if_needed(self, thesis_id: int) -> None:
        """Kiểm tra invalidation conditions sau mỗi score recompute.

        Không raise — failure ở đây không block caller.
        """
        try:
            thesis = await self._repo.get_by_id(thesis_id)
            if thesis is None or thesis.status != ThesisStatus.ACTIVE:
                return
            result = InvalidationService().check(thesis, thesis.score or 0.0)
            if result.should_invalidate:
                thesis.status = ThesisStatus.INVALIDATED
                thesis.closed_at = datetime.now(UTC)
                await self._repo.save(thesis)
                logger.warning(
                    "thesis.auto_invalidated",
                    thesis_id=thesis_id,
                    reason=result.reason,
                    invalid_assumptions=result.invalid_assumptions,
                    score=result.score,
                )
        except Exception:
            logger.exception("thesis.auto_invalidate_failed", thesis_id=thesis_id)

    async def _get_thesis(self, thesis_id: int) -> Thesis:
        """Load thesis by id — không check ownership (caller đã check)."""
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            from src.thesis.dtos import ThesisNotFoundError
            raise ThesisNotFoundError(f"Thesis {thesis_id} not found")
        return thesis

    # ------------------------------------------------------------------
    # Assumption CRUD
    # ------------------------------------------------------------------

    async def add_assumption(
        self, thesis_id: int, inp: AddAssumptionInput
    ) -> Assumption:
        assumption = Assumption(
            thesis_id=thesis_id,
            description=inp.description,
            status=inp.status,
            note=inp.note,
        )
        await self._repo.save_assumption(assumption)
        logger.info("assumption.added", thesis_id=thesis_id, assumption_id=assumption.id)
        await self._recompute_score(thesis_id)
        await self._auto_invalidate_if_needed(thesis_id)
        return assumption

    async def update_assumption(
        self,
        thesis_id: int,
        assumption_id: int,
        inp: UpdateAssumptionInput,
    ) -> Assumption:
        from src.thesis.dtos import AssumptionNotFoundError
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
        await self._auto_invalidate_if_needed(thesis_id)
        return assumption

    async def delete_assumption(self, thesis_id: int, assumption_id: int) -> None:
        from src.thesis.dtos import AssumptionNotFoundError
        assumption = await self._repo.get_assumption_by_id(assumption_id, thesis_id)
        if assumption is None:
            raise AssumptionNotFoundError(
                f"Assumption {assumption_id} not found in thesis {thesis_id}"
            )
        await self._repo.delete_assumption(assumption)
        logger.info("assumption.deleted", assumption_id=assumption_id, thesis_id=thesis_id)
        await self._recompute_score(thesis_id)
        await self._auto_invalidate_if_needed(thesis_id)

    # ------------------------------------------------------------------
    # Catalyst CRUD
    # ------------------------------------------------------------------

    async def add_catalyst(
        self, thesis_id: int, inp: AddCatalystInput
    ) -> Catalyst:
        catalyst = Catalyst(
            thesis_id=thesis_id,
            description=inp.description,
            status=inp.status,
            expected_date=inp.expected_date,
            note=inp.note,
        )
        await self._repo.save_catalyst(catalyst)
        logger.info("catalyst.added", thesis_id=thesis_id, catalyst_id=catalyst.id)
        await self._recompute_score(thesis_id)
        await self._auto_invalidate_if_needed(thesis_id)
        return catalyst

    async def add_catalyst_from_timeline(
        self,
        thesis_id: int,
        user_id: str | None,
        description: str,
        timeline: str | None,
        note: str | None = None,
        status: CatalystStatus = CatalystStatus.PENDING,
    ) -> Catalyst:
        """Convenience helper: parse AI timeline string rồi gọi add_catalyst."""
        return await self.add_catalyst(
            thesis_id=thesis_id,
            inp=AddCatalystInput(
                description=description,
                status=status,
                expected_date=parse_timeline_to_date(timeline),
                note=note,
            ),
        )

    async def update_catalyst(
        self,
        thesis_id: int,
        catalyst_id: int,
        inp: UpdateCatalystInput,
    ) -> Catalyst:
        from src.thesis.dtos import CatalystNotFoundError
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
        await self._auto_invalidate_if_needed(thesis_id)
        return catalyst

    async def delete_catalyst(self, thesis_id: int, catalyst_id: int) -> None:
        from src.thesis.dtos import CatalystNotFoundError
        catalyst = await self._repo.get_catalyst_by_id(catalyst_id, thesis_id)
        if catalyst is None:
            raise CatalystNotFoundError(
                f"Catalyst {catalyst_id} not found in thesis {thesis_id}"
            )
        await self._repo.delete_catalyst(catalyst)
        logger.info("catalyst.deleted", catalyst_id=catalyst_id, thesis_id=thesis_id)
        await self._recompute_score(thesis_id)
        await self._auto_invalidate_if_needed(thesis_id)

    # ------------------------------------------------------------------
    # Auto-maintenance (Wave 5)
    # ------------------------------------------------------------------

    async def auto_expire_overdue_catalysts(self, user_id: str) -> int:
        """Chuyển catalyst PENDING đã qua expected_date → EXPIRED.

        Chạy mỗi ngày lúc 08:30 ICT (trước morning brief) bởi
        ThesisMaintenanceScheduler. Không raise — failure của từng
        thesis được log và skip, không block các thesis khác.

        Returns:
            Số catalyst bị expire trong lần chạy này.
        """
        now = datetime.now(UTC)
        theses = await self._repo.list_active_for_user(user_id)
        expired_count = 0
        affected_thesis_ids: set[int] = set()

        for thesis in theses:
            for catalyst in thesis.catalysts:
                if (
                    catalyst.status == CatalystStatus.PENDING
                    and catalyst.expected_date is not None
                    and catalyst.expected_date < now
                ):
                    catalyst.status = CatalystStatus.EXPIRED
                    await self._repo.save_catalyst(catalyst)
                    expired_count += 1
                    affected_thesis_ids.add(thesis.id)
                    logger.info(
                        "catalyst.auto_expired",
                        catalyst_id=catalyst.id,
                        thesis_id=thesis.id,
                        expected_date=catalyst.expected_date.isoformat(),
                    )

        # Recompute score + check invalidation cho các thesis bị ảnh hưởng
        for thesis_id in affected_thesis_ids:
            try:
                await self._recompute_score(thesis_id)
                await self._auto_invalidate_if_needed(thesis_id)
            except Exception:
                logger.exception(
                    "catalyst.auto_expire.score_recompute_failed",
                    thesis_id=thesis_id,
                )

        if expired_count:
            logger.info(
                "catalyst.auto_expire.done",
                user_id=user_id,
                expired_count=expired_count,
                affected_theses=len(affected_thesis_ids),
            )

        return expired_count

    # ------------------------------------------------------------------
    # Recommendation
    # ------------------------------------------------------------------

    async def apply_recommendation(
        self,
        thesis_id: int,
        recommendation_id: int,
        accept: bool,
    ) -> None:
        """Apply hoặc reject một AI recommendation.

        - accept=True  → apply status change lên assumption/catalyst, mark ACCEPTED
        - accept=False → mark REJECTED, không thay đổi gì khác
        acted_at được set trong cả 2 nhánh để đảm bảo audit trail.
        """
        rec = await self._repo.get_recommendation_by_id(recommendation_id)
        if rec is None:
            from src.thesis.dtos import RecommendationNotFoundError
            raise RecommendationNotFoundError(
                f"Recommendation {recommendation_id} not found"
            )

        now = datetime.now(UTC)

        if not accept:
            rec.status = RecommendationStatus.REJECTED
            rec.acted_at = now
            await self._repo.save_recommendation(rec)
            logger.info("recommendation.rejected", recommendation_id=recommendation_id)
            return

        # Accept: apply status change
        from src.thesis.models import Assumption, AssumptionStatus, Catalyst, CatalystStatus

        if rec.target_type == "assumption":
            target = await self._repo.get_assumption_by_id(rec.target_id, thesis_id)
            if target is not None:
                try:
                    target.status = AssumptionStatus(rec.recommended_status.lower())
                    await self._repo.save_assumption(target)
                except ValueError:
                    logger.warning(
                        "recommendation.apply.invalid_assumption_status",
                        recommended_status=rec.recommended_status,
                        recommendation_id=recommendation_id,
                    )
        elif rec.target_type == "catalyst":
            target = await self._repo.get_catalyst_by_id(rec.target_id, thesis_id)
            if target is not None:
                try:
                    target.status = CatalystStatus(rec.recommended_status.lower())
                    await self._repo.save_catalyst(target)
                except ValueError:
                    logger.warning(
                        "recommendation.apply.invalid_catalyst_status",
                        recommended_status=rec.recommended_status,
                        recommendation_id=recommendation_id,
                    )

        rec.status = RecommendationStatus.ACCEPTED
        rec.acted_at = now
        await self._repo.save_recommendation(rec)
        logger.info("recommendation.accepted", recommendation_id=recommendation_id)

        await self._recompute_score(thesis_id)
        await self._auto_invalidate_if_needed(thesis_id)
