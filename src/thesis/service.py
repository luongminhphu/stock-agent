"""Thesis service — lifecycle operations for the thesis segment.

Owner: thesis segment.
Entry point duy nhất cho bot commands và API routes.

ThesisService chỉ chịu trách nhiệm thesis lifecycle:
  create / update / close / review / debate / score thesis.
  Đọc Assumption, Catalyst, Review, DebateResult.

Không chứa:
  - Quote/market data logic (thuộc market segment)
  - Brief generation logic (thuộc briefing segment)
  - Dashboard projection (thuộc readmodel segment)
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.thesis_debate_agent import ThesisDebateAgent
from src.ai.agents.thesis_judge_agent import ThesisJudgeAgent
from src.ai.agents.thesis_review_agent import ThesisReviewAgent
from src.platform.logging import get_logger
from src.thesis.models import (
    Assumption,
    Catalyst,
    DebateResult,
    Thesis,
    ThesisScore,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.thesis.schemas import (
    AddAssumptionInput,
    AddCatalystInput,
    CreateThesisInput,
    ThesisDebateInput,
    ThesisReviewInput,
    ThesisScoreInput,
    UpdateAssumptionInput,
    UpdateCatalystInput,
    UpdateThesisInput,
)
from src.thesis.thesis_components import ThesisComponents

logger = get_logger(__name__)

_DEFAULT_USER_ID = "default_user"


def _resolve_user_id(user_id: str | None) -> str:
    return user_id if user_id is not None else _DEFAULT_USER_ID


def _require_session(session: Any, method_name: str) -> None:
    if session is None:
        raise RuntimeError(
            f"ThesisService.{method_name} requires a DB session. "
            "Pass session= when constructing ThesisService."
        )


def _thesis_overview(thesis: Thesis) -> dict[str, Any]:
    """Compact dict for logging / debug use."""
    return {
        "id": thesis.id,
        "ticker": thesis.ticker,
        "status": thesis.status,
        "user_id": thesis.user_id,
    }


class ThesisService:
    """High-level orchestration layer for the thesis segment.

    All public methods are async.  The service is stateless across calls —
    it holds no mutable state other than the injected collaborators.

    Args:
        session: SQLAlchemy AsyncSession.  Required for all persistence
            operations.  Pass None only in unit-test stubs.
        thesis_review_agent: Optional AI agent for review generation.
            Falls back to a lightweight stub when not provided.
        thesis_debate_agent: Optional AI agent for debate generation.
        thesis_judge_agent: Optional AI agent for scoring.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        thesis_review_agent: ThesisReviewAgent | None = None,
        thesis_debate_agent: ThesisDebateAgent | None = None,
        thesis_judge_agent: ThesisJudgeAgent | None = None,
    ) -> None:
        self._session = session
        self._repo = ThesisRepository(session) if session is not None else None
        self._components = ThesisComponents(
            session=session,
            thesis_review_agent=thesis_review_agent,
            thesis_debate_agent=thesis_debate_agent,
            thesis_judge_agent=thesis_judge_agent,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, thesis_id: int, user_id: str) -> Thesis:
        """Fetch thesis and assert ownership."""
        _require_session(self._session, "_get_owned")
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            raise ValueError(f"Thesis {thesis_id} not found")
        if thesis.user_id != user_id:
            raise PermissionError(
                f"User {user_id!r} does not own thesis {thesis_id}"
            )
        return thesis

    @staticmethod
    def _assert_mutable(thesis: Thesis) -> None:
        if thesis.status not in (ThesisStatus.ACTIVE, ThesisStatus.DRAFT):
            raise ValueError(
                f"Thesis {thesis.id} is {thesis.status!r} and cannot be modified"
            )

    # ------------------------------------------------------------------
    # Thesis CRUD
    # ------------------------------------------------------------------

    async def create(
        self, user_id: str, inp: CreateThesisInput
    ) -> Thesis:
        _require_session(self._session, "create")
        thesis = Thesis(
            user_id=user_id,
            ticker=inp.ticker.upper(),
            entry_thesis=inp.entry_thesis,
            target_price=inp.target_price,
            stop_loss=inp.stop_loss,
            time_horizon=inp.time_horizon,
            status=ThesisStatus.ACTIVE,
        )
        self._session.add(thesis)
        await self._session.flush()  # populate thesis.id
        logger.info("thesis.created", **_thesis_overview(thesis))
        return thesis

    async def update(
        self, thesis_id: int, user_id: str, inp: UpdateThesisInput
    ) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        update_data = inp.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(thesis, field, value)
        logger.info("thesis.updated", **_thesis_overview(thesis), fields=list(update_data))
        return thesis

    async def close(
        self,
        thesis_id: int,
        user_id: str,
        outcome: str = "completed",
    ) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        thesis.status = ThesisStatus.CLOSED
        logger.info("thesis.closed", **_thesis_overview(thesis), outcome=outcome)
        return thesis

    async def reopen(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        if thesis.status != ThesisStatus.CLOSED:
            raise ValueError(f"Thesis {thesis_id} is not closed")
        thesis.status = ThesisStatus.ACTIVE
        logger.info("thesis.reopened", **_thesis_overview(thesis))
        return thesis

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get(self, thesis_id: int, user_id: str) -> Thesis:
        return await self._get_owned(thesis_id, user_id)

    async def list_for_user(
        self,
        user_id: str | None = None,
        status: ThesisStatus | None = None,
    ) -> list[Thesis]:
        user_id = _resolve_user_id(user_id)
        return await self._repo.list_by_user(user_id, status)

    async def list_active(self, user_id: str) -> list[Thesis]:
        """Return all ACTIVE theses for a user.

        Alias for list_for_user(user_id, status=ThesisStatus.ACTIVE).
        Called by briefing context_builder for thesis health context.
        """
        return await self.list_for_user(user_id=user_id, status=ThesisStatus.ACTIVE)

    async def get_active_thesis_id_for_ticker(
        self,
        ticker: str,
        user_id: str | None = None,
    ) -> str | None:
        """Return str(thesis.id) of the first ACTIVE thesis for ticker."""
        resolved = _resolve_user_id(user_id)
        theses = await self._repo.list_active_by_ticker(ticker)
        user_theses = [t for t in theses if t.user_id == resolved]
        if not user_theses:
            return None
        return str(user_theses[0].id)

    # ------------------------------------------------------------------
    # Assumption proxy
    # ------------------------------------------------------------------

    async def add_assumption(
        self, thesis_id: int, user_id: str, inp: AddAssumptionInput
    ) -> Assumption:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        return await self._components.add_assumption(thesis_id, inp)

    async def update_assumption(
        self, thesis_id: int, assumption_id: int, user_id: str, inp: UpdateAssumptionInput
    ) -> Assumption:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        return await self._components.update_assumption(assumption_id, inp)

    async def delete_assumption(
        self, thesis_id: int, assumption_id: int, user_id: str
    ) -> None:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        await self._components.delete_assumption(assumption_id)

    async def list_assumptions(self, thesis_id: int, user_id: str) -> list[Assumption]:
        await self._get_owned(thesis_id, user_id)
        return await self._components.list_assumptions(thesis_id)

    # ------------------------------------------------------------------
    # Catalyst proxy
    # ------------------------------------------------------------------

    async def add_catalyst(
        self, thesis_id: int, user_id: str, inp: AddCatalystInput
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        return await self._components.add_catalyst(thesis_id, inp)

    async def update_catalyst(
        self, thesis_id: int, catalyst_id: int, user_id: str, inp: UpdateCatalystInput
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        return await self._components.update_catalyst(catalyst_id, inp)

    async def delete_catalyst(
        self, thesis_id: int, catalyst_id: int, user_id: str
    ) -> None:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        await self._components.delete_catalyst(catalyst_id)

    async def list_catalysts(self, thesis_id: int, user_id: str) -> list[Catalyst]:
        await self._get_owned(thesis_id, user_id)
        return await self._components.list_catalysts(thesis_id)

    # ------------------------------------------------------------------
    # Review proxy
    # ------------------------------------------------------------------

    async def add_review(
        self, thesis_id: int, user_id: str, inp: ThesisReviewInput
    ) -> Any:
        thesis = await self._get_owned(thesis_id, user_id)
        return await self._components.add_review(thesis, inp)

    async def list_reviews(self, thesis_id: int, user_id: str) -> list[Any]:
        await self._get_owned(thesis_id, user_id)
        return await self._components.list_reviews(thesis_id)

    # ------------------------------------------------------------------
    # Debate proxy
    # ------------------------------------------------------------------

    async def run_debate(
        self, thesis_id: int, user_id: str, inp: ThesisDebateInput
    ) -> DebateResult:
        thesis = await self._get_owned(thesis_id, user_id)
        return await self._components.run_debate(thesis, inp)

    async def list_debates(self, thesis_id: int, user_id: str) -> list[DebateResult]:
        await self._get_owned(thesis_id, user_id)
        return await self._components.list_debates(thesis_id)

    # ------------------------------------------------------------------
    # Score proxy
    # ------------------------------------------------------------------

    async def score(
        self, thesis_id: int, user_id: str, inp: ThesisScoreInput | None = None
    ) -> ThesisScore:
        thesis = await self._get_owned(thesis_id, user_id)
        return await self._components.score(thesis, inp)

    async def list_scores(self, thesis_id: int, user_id: str) -> list[ThesisScore]:
        await self._get_owned(thesis_id, user_id)
        return await self._components.list_scores(thesis_id)

    # ------------------------------------------------------------------
    # Bulk / cross-thesis helpers
    # ------------------------------------------------------------------

    async def get_thesis_context_for_ticker(
        self,
        ticker: str,
        user_id: str | None = None,
    ) -> str:
        """Return a compact text context block for the active thesis on ticker.

        Used by briefing and pretrade agents to inject thesis narrative.
        Returns empty string when no active thesis exists.
        """
        resolved = _resolve_user_id(user_id)
        theses = await self._repo.list_active_by_ticker(ticker)
        user_theses = [t for t in theses if t.user_id == resolved]
        if not user_theses:
            return ""
        thesis = user_theses[0]
        parts = [f"Thesis [{thesis.id}] {thesis.ticker}: {thesis.entry_thesis}"]
        if thesis.target_price:
            parts.append(f"Target: {thesis.target_price}")
        if thesis.stop_loss:
            parts.append(f"Stop: {thesis.stop_loss}")
        if thesis.time_horizon:
            parts.append(f"Horizon: {thesis.time_horizon}")
        return " | ".join(parts)

    async def batch_get_thesis_contexts(
        self,
        tickers: list[str],
        user_id: str | None = None,
    ) -> dict[str, str]:
        """Return thesis context strings for multiple tickers concurrently."""
        tasks = [
            self.get_thesis_context_for_ticker(ticker, user_id)
            for ticker in tickers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, str] = {}
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.warning(
                    "thesis.batch_context_failed",
                    ticker=ticker,
                    error=str(result),
                )
                out[ticker] = ""
            else:
                out[ticker] = result  # type: ignore[assignment]
        return out

    async def get_thesis_health(
        self,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a health snapshot of all active theses for the user.

        Each dict contains: id, ticker, status, assumption_count,
        last_review_at, days_since_review.
        Used by briefing context_builder for the thesis health block.
        """
        resolved = _resolve_user_id(user_id)
        try:
            theses = await self.list_active(user_id=resolved)
        except Exception as exc:
            logger.warning(
                "thesis_health.list_active_failed",
                user_id=resolved,
                error=str(exc),
            )
            return []

        results: list[dict[str, Any]] = []
        for thesis in theses:
            try:
                assumptions = await self._components.list_assumptions(thesis.id)
                reviews = await self._components.list_reviews(thesis.id)
            except Exception as exc:
                logger.warning(
                    "thesis_health.detail_failed",
                    thesis_id=thesis.id,
                    error=str(exc),
                )
                assumptions, reviews = [], []

            last_review_at = None
            if reviews:
                last_review_at = max(
                    (getattr(r, "created_at", None) for r in reviews),
                    default=None,
                )

            days_since_review: int | None = None
            if last_review_at is not None:
                import datetime  # noqa: PLC0415

                now = datetime.datetime.now(tz=datetime.timezone.utc)
                if last_review_at.tzinfo is None:
                    last_review_at = last_review_at.replace(
                        tzinfo=datetime.timezone.utc
                    )
                days_since_review = (now - last_review_at).days

            results.append(
                {
                    "id": thesis.id,
                    "ticker": thesis.ticker,
                    "status": thesis.status,
                    "entry_thesis": thesis.entry_thesis,
                    "target_price": thesis.target_price,
                    "stop_loss": thesis.stop_loss,
                    "time_horizon": thesis.time_horizon,
                    "assumption_count": len(assumptions),
                    "last_review_at": last_review_at,
                    "days_since_review": days_since_review,
                }
            )
        return results

    # ------------------------------------------------------------------
    # Convenience wrappers used by briefing / signal agents
    # ------------------------------------------------------------------

    @functools.lru_cache(maxsize=None)
    def _cached_active_tickers(self) -> frozenset[str]:
        """NOT async — do not call directly. Use get_active_tickers instead."""
        raise NotImplementedError("Use get_active_tickers (async)")

    async def get_active_tickers(self, user_id: str | None = None) -> list[str]:
        """Return tickers of all active theses for the user."""
        resolved = _resolve_user_id(user_id)
        theses = await self.list_active(resolved)
        return [t.ticker for t in theses]
