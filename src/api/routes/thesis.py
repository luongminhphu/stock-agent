"""Thesis routes — CRUD for thesis, assumption, catalyst + AI review + AI suggest.

Owner: api segment.
No business logic here — delegates entirely to ThesisService, ScoringService,
ReviewService, ThesisSuggestAgent.

Endpoints:
    POST   /thesis/suggest                                — AI draft thesis for a ticker (no persist)
    POST   /thesis                                        — create thesis
    GET    /thesis                                        — list thesis
    GET    /thesis/{thesis_id}                            — get thesis detail
    PATCH  /thesis/{thesis_id}                            — update thesis
    DELETE /thesis/{thesis_id}                            — delete thesis (hard)
    POST   /thesis/{thesis_id}/close                      — close thesis
    POST   /thesis/{thesis_id}/invalidate                 — invalidate thesis
    GET    /thesis/{thesis_id}/score                      — health score breakdown
    GET    /thesis/{thesis_id}/assumptions                — list assumptions
    POST   /thesis/{thesis_id}/assumptions                — add assumption
    GET    /thesis/{thesis_id}/assumptions/{id}           — get assumption
    PATCH  /thesis/{thesis_id}/assumptions/{id}           — update assumption
    DELETE /thesis/{thesis_id}/assumptions/{id}           — delete assumption
    GET    /thesis/{thesis_id}/catalysts                  — list catalysts
    POST   /thesis/{thesis_id}/catalysts                  — add catalyst
    GET    /thesis/{thesis_id}/catalysts/{id}             — get catalyst
    PATCH  /thesis/{thesis_id}/catalysts/{id}             — update catalyst
    DELETE /thesis/{thesis_id}/catalysts/{id}             — delete catalyst
    POST   /thesis/{thesis_id}/review                     — trigger AI review
    GET    /thesis/{thesis_id}/reviews                    — list past reviews
    GET    /thesis/{thesis_id}/reviews/latest             — latest review only
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.suggest_agent import ThesisSuggestAgent
from src.ai.client import PerplexityError
from src.ai.schemas import ThesisSuggestionResult
from src.api.deps import (
    get_current_user_id,
    get_db,
    get_review_service,
    get_thesis_service,
    get_thesis_suggest_agent,
)
from src.api.dto.thesis import (
    AssumptionCreateRequest,
    AssumptionListResponse,
    AssumptionResponse,
    AssumptionUpdateRequest,
    CatalystCreateRequest,
    CatalystListResponse,
    CatalystResponse,
    CatalystUpdateRequest,
    HealthScoreBreakdown,
    HealthScoreResponse,
    ThesisCreateRequest,
    ThesisListResponse,
    ThesisResponse,
    ThesisReviewListResponse,
    ThesisReviewResponse,
    ThesisUpdateRequest,
)
from src.thesis.models import ThesisStatus
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.scoring_service import ScoringService, score_tier
from src.thesis.service import (
    AddAssumptionInput,
    AddCatalystInput,
    AssumptionNotFoundError,
    CatalystNotFoundError,
    CreateThesisInput,
    ThesisAlreadyClosedError,
    ThesisNotFoundError,
    ThesisService,
    UpdateAssumptionInput,
    UpdateCatalystInput,
    UpdateThesisInput,
    parse_timeline_to_date,
)

router = APIRouter(prefix="/thesis", tags=["thesis"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# ---------------------------------------------------------------------------
# AI Suggest (must be declared BEFORE /{thesis_id} routes to avoid conflict)
# ---------------------------------------------------------------------------


@router.post("/suggest", response_model=ThesisSuggestionResult)
async def suggest_thesis(
    ticker: str = Query(..., description="Mã cổ phiếu, VD: VNM, HPG, MWG"),
    _user_id: str = Depends(get_current_user_id),
    agent: ThesisSuggestAgent = Depends(get_thesis_suggest_agent),  # type: ignore[type-arg]
) -> ThesisSuggestionResult:
    """Ask AI to draft an investment thesis for a ticker.

    Returns a ThesisSuggestionResult — a *draft* for the investor to review.
    Nothing is persisted. The investor must confirm and call POST /thesis to save.

    Price hints (entry_price_hint, target_price_hint, stop_loss_hint) are
    AI estimates only — do NOT auto-save without user confirmation.
    """
    try:
        return await agent.suggest(ticker)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"AI response could not be parsed: {exc}",
        )
    except PerplexityError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI suggest failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Thesis CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ThesisResponse, status_code=status.HTTP_201_CREATED)
async def create_thesis(
    body: ThesisCreateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> ThesisResponse:
    """Create a new thesis with optional initial assumptions and catalysts.

    ThesisCreateRequest.catalysts is list[str] (plain descriptions).
    We map each string → AddCatalystInput here at the route boundary so the
    service layer never has to deal with raw strings.

    If the DTO later evolves to carry expected_timeline strings (e.g. from
    the AI suggest confirm flow), parse_timeline_to_date is already imported
    and can be wired in without touching the service.
    """
    catalyst_inputs: list[AddCatalystInput] = [
        AddCatalystInput(description=desc)
        for desc in (body.catalysts or [])
    ]

    thesis = await svc.create(
        CreateThesisInput(
            user_id=user_id,
            ticker=body.ticker,
            title=body.title,
            summary=body.summary,
            entry_price=body.entry_price,
            target_price=body.target_price,
            stop_loss=body.stop_loss,
            assumptions=body.assumptions or None,
            catalysts=catalyst_inputs or None,
        )
    )
    return ThesisResponse.model_validate(thesis)


@router.get("", response_model=ThesisListResponse)
async def list_theses(
    status_filter: str | None = Query(default=None, alias="status"),
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> ThesisListResponse:
    """List all theses for the current user, optionally filtered by status."""
    status_enum: ThesisStatus | None = None
    if status_filter:
        try:
            status_enum = ThesisStatus(status_filter.lower())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status: {status_filter}. Valid: {[s.value for s in ThesisStatus]}",
            )
    items = await svc.list_for_user(user_id, status_enum)
    return ThesisListResponse(
        items=[ThesisResponse.model_validate(t) for t in items],
        total=len(items),
    )


@router.get("/{thesis_id}", response_model=ThesisResponse)
async def get_thesis(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> ThesisResponse:
    """Get full thesis detail including assumptions and catalysts + health score."""
    try:
        thesis = await svc.get(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)

    # Nếu đã có thesis.score, dùng luôn; nếu chưa, fallback tính lại
    scoring = ScoringService()
    total, breakdown = scoring.compute_with_breakdown(thesis)
    tier_label, tier_icon = score_tier(total)

    # Build DTO bằng tay để fill thêm trường mới
    base = ThesisResponse.model_validate(thesis)
    base.score = thesis.score if thesis.score is not None else total
    base.score_tier = tier_label
    base.score_tier_icon = tier_icon
    base.score_breakdown = HealthScoreBreakdown(
        assumption_health=breakdown["assumption_health"],
        catalyst_progress=breakdown["catalyst_progress"],
        risk_reward=breakdown["risk_reward"],
        review_confidence=breakdown["review_confidence"],
    )
    return base


@router.patch("/{thesis_id}", response_model=ThesisResponse)
async def update_thesis(
    thesis_id: int,
    body: ThesisUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> ThesisResponse:
    """Partially update thesis header fields (title, summary, prices)."""
    try:
        thesis = await svc.update(
            thesis_id,
            user_id,
            UpdateThesisInput(
                title=body.title,
                summary=body.summary,
                entry_price=body.entry_price,
                target_price=body.target_price,
                stop_loss=body.stop_loss,
            ),
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc)
    return ThesisResponse.model_validate(thesis)


@router.delete("/{thesis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thesis(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> None:
    """Hard delete a thesis and all its children."""
    try:
        await svc.delete(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)


@router.post("/{thesis_id}/close", response_model=ThesisResponse)
async def close_thesis(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> ThesisResponse:
    """Mark thesis as closed."""
    try:
        thesis = await svc.close(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc)
    return ThesisResponse.model_validate(thesis)


@router.post("/{thesis_id}/invalidate", response_model=ThesisResponse)
async def invalidate_thesis(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> ThesisResponse:
    """Mark thesis as invalidated."""
    try:
        thesis = await svc.invalidate(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc)
    return ThesisResponse.model_validate(thesis)


# ---------------------------------------------------------------------------
# Health Score
# ---------------------------------------------------------------------------


@router.get("/{thesis_id}/score", response_model=HealthScoreResponse)
async def get_health_score(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> HealthScoreResponse:
    """Return composite health score (0-100) with 4-dimension breakdown."""
    try:
        thesis = await svc.get(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)

    scoring = ScoringService()
    total, breakdown = scoring.compute_with_breakdown(thesis)
    return HealthScoreResponse(
        thesis_id=thesis_id,
        total=total,
        breakdown=HealthScoreBreakdown(
            assumption_health=breakdown["assumption_health"],
            catalyst_progress=breakdown["catalyst_progress"],
            risk_reward=breakdown["risk_reward"],
            review_confidence=breakdown["review_confidence"],
        ),
    )


# ---------------------------------------------------------------------------
# Assumption CRUD
# ---------------------------------------------------------------------------


@router.get("/{thesis_id}/assumptions", response_model=AssumptionListResponse)
async def list_assumptions(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> AssumptionListResponse:
    """List all assumptions for a thesis."""
    try:
        thesis = await svc.get(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    items = thesis.assumptions or []
    return AssumptionListResponse(
        items=[AssumptionResponse.model_validate(a) for a in items],
        total=len(items),
    )


@router.post(
    "/{thesis_id}/assumptions",
    response_model=AssumptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_assumption(
    thesis_id: int,
    body: AssumptionCreateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> AssumptionResponse:
    """Add a new assumption to a thesis."""
    try:
        assumption = await svc.add_assumption(
            thesis_id,
            user_id,
            AddAssumptionInput(
                description=body.description,
                status=body.status,
                note=body.note,
            ),
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc)
    return AssumptionResponse.model_validate(assumption)


@router.get(
    "/{thesis_id}/assumptions/{assumption_id}",
    response_model=AssumptionResponse,
)
async def get_assumption(
    thesis_id: int,
    assumption_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> AssumptionResponse:
    """Get a single assumption by id."""
    try:
        thesis = await svc.get(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    assumption = next((a for a in thesis.assumptions if a.id == assumption_id), None)
    if assumption is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assumption {assumption_id} not found in thesis {thesis_id}.",
        )
    return AssumptionResponse.model_validate(assumption)


@router.patch(
    "/{thesis_id}/assumptions/{assumption_id}",
    response_model=AssumptionResponse,
)
async def update_assumption(
    thesis_id: int,
    assumption_id: int,
    body: AssumptionUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> AssumptionResponse:
    """Update description, status, or note of an assumption."""
    try:
        assumption = await svc.update_assumption(
            thesis_id,
            assumption_id,
            user_id,
            UpdateAssumptionInput(
                description=body.description,
                status=body.status,
                note=body.note,
            ),
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except AssumptionNotFoundError as exc:
        raise _not_found(exc)
    return AssumptionResponse.model_validate(assumption)


@router.delete(
    "/{thesis_id}/assumptions/{assumption_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_assumption(
    thesis_id: int,
    assumption_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> None:
    """Delete an assumption from a thesis."""
    try:
        await svc.delete_assumption(thesis_id, assumption_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except AssumptionNotFoundError as exc:
        raise _not_found(exc)


# ---------------------------------------------------------------------------
# Catalyst CRUD
# ---------------------------------------------------------------------------


@router.get("/{thesis_id}/catalysts", response_model=CatalystListResponse)
async def list_catalysts(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> CatalystListResponse:
    """List all catalysts for a thesis."""
    try:
        thesis = await svc.get(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    items = thesis.catalysts or []
    return CatalystListResponse(
        items=[CatalystResponse.model_validate(c) for c in items],
        total=len(items),
    )


@router.post(
    "/{thesis_id}/catalysts",
    response_model=CatalystResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_catalyst(
    thesis_id: int,
    body: CatalystCreateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> CatalystResponse:
    """Add a new catalyst to a thesis."""
    try:
        catalyst = await svc.add_catalyst(
            thesis_id,
            user_id,
            AddCatalystInput(
                description=body.description,
                status=body.status,
                expected_date=body.expected_date,
                note=body.note,
            ),
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc)
    return CatalystResponse.model_validate(catalyst)


@router.get(
    "/{thesis_id}/catalysts/{catalyst_id}",
    response_model=CatalystResponse,
)
async def get_catalyst(
    thesis_id: int,
    catalyst_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> CatalystResponse:
    """Get a single catalyst by id."""
    try:
        thesis = await svc.get(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    catalyst = next((c for c in thesis.catalysts if c.id == catalyst_id), None)
    if catalyst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalyst {catalyst_id} not found in thesis {thesis_id}.",
        )
    return CatalystResponse.model_validate(catalyst)


@router.patch(
    "/{thesis_id}/catalysts/{catalyst_id}",
    response_model=CatalystResponse,
)
async def update_catalyst(
    thesis_id: int,
    catalyst_id: int,
    body: CatalystUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> CatalystResponse:
    """Update description, status, dates, or note of a catalyst."""
    try:
        catalyst = await svc.update_catalyst(
            thesis_id,
            catalyst_id,
            user_id,
            UpdateCatalystInput(
                description=body.description,
                status=body.status,
                expected_date=body.expected_date,
                triggered_at=body.triggered_at,
                note=body.note,
            ),
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except CatalystNotFoundError as exc:
        raise _not_found(exc)
    return CatalystResponse.model_validate(catalyst)


@router.delete(
    "/{thesis_id}/catalysts/{catalyst_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_catalyst(
    thesis_id: int,
    catalyst_id: int,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> None:
    """Delete a catalyst from a thesis."""
    try:
        await svc.delete_catalyst(thesis_id, catalyst_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except CatalystNotFoundError as exc:
        raise _not_found(exc)


# ---------------------------------------------------------------------------
# AI Review endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{thesis_id}/review",
    response_model=ThesisReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_review(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    review_svc: ReviewService = Depends(get_review_service),
) -> ThesisReviewResponse:
    """Trigger an AI review for a thesis."""
    try:
        review = await review_svc.review_thesis(thesis_id=thesis_id, user_id=user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    except ReviewNotAllowedError as exc:
        raise _conflict(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI review failed: {exc}",
        )
    return ThesisReviewResponse.model_validate(review)


@router.get("/{thesis_id}/reviews", response_model=ThesisReviewListResponse)
async def list_reviews(
    thesis_id: int,
    limit: int = 10,
    user_id: str = Depends(get_current_user_id),
    review_svc: ReviewService = Depends(get_review_service),
) -> ThesisReviewListResponse:
    """Return recent AI reviews for a thesis."""
    try:
        reviews = await review_svc.list_reviews(
            thesis_id=thesis_id, user_id=user_id, limit=min(limit, 50)
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    return ThesisReviewListResponse(
        thesis_id=thesis_id,
        reviews=[ThesisReviewResponse.model_validate(r) for r in reviews],
        total=len(reviews),
    )


@router.get("/{thesis_id}/reviews/latest", response_model=ThesisReviewResponse)
async def get_latest_review(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    review_svc: ReviewService = Depends(get_review_service),
) -> ThesisReviewResponse:
    """Return the most recent AI review for a thesis."""
    try:
        reviews = await review_svc.list_reviews(
            thesis_id=thesis_id, user_id=user_id, limit=1
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc)
    if not reviews:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No reviews found for thesis {thesis_id}.",
        )
    return ThesisReviewResponse.model_validate(reviews[0])
