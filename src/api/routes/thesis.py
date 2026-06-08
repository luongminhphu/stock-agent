"""Thesis routes — CRUD for thesis, assumption, catalyst + AI review + AI suggest + AI debate.

Owner: api segment.
No business logic here — delegates entirely to ThesisService, ScoringService,
ReviewService, ThesisSuggestAgent, ThesisDebateAgent.

Endpoints:
    POST   /thesis/suggest                                        — AI draft thesis for a ticker (no persist)
    POST   /thesis                                                — create thesis
    GET    /thesis                                                — list thesis
    GET    /thesis/{thesis_id}                                    — get thesis detail
    PATCH  /thesis/{thesis_id}                                    — update thesis
    DELETE /thesis/{thesis_id}                                    — delete thesis (hard)
    POST   /thesis/{thesis_id}/close                              — close thesis
    POST   /thesis/{thesis_id}/invalidate                         — invalidate thesis
    GET    /thesis/{thesis_id}/score                              — health score breakdown
    GET    /thesis/{thesis_id}/conviction-timeline                — conviction score over time
    GET    /thesis/{thesis_id}/assumptions                        — list assumptions
    POST   /thesis/{thesis_id}/assumptions                        — add assumption
    GET    /thesis/{thesis_id}/assumptions/{id}                   — get assumption
    PATCH  /thesis/{thesis_id}/assumptions/{id}                   — update assumption
    DELETE /thesis/{thesis_id}/assumptions/{id}                   — delete assumption
    GET    /thesis/{thesis_id}/catalysts                          — list catalysts
    POST   /thesis/{thesis_id}/catalysts                          — add catalyst
    GET    /thesis/{thesis_id}/catalysts/{id}                     — get catalyst
    PATCH  /thesis/{thesis_id}/catalysts/{id}                     — update catalyst
    DELETE /thesis/{thesis_id}/catalysts/{id}                     — delete catalyst
    POST   /thesis/{thesis_id}/review                             — trigger AI review
    GET    /thesis/{thesis_id}/reviews                            — list past reviews
    GET    /thesis/{thesis_id}/reviews/latest                     — latest review only
    GET    /thesis/{thesis_id}/recommendations                    — list PENDING AI recommendations
    POST   /thesis/{thesis_id}/recommendations/{rec_id}/apply     — accept or reject a recommendation
    POST   /thesis/{thesis_id}/debate                             — trigger AI debate (devil's advocate)
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.ai.agents.suggest_agent import ThesisSuggestAgent
from src.ai.agents.thesis_debate import ThesisDebateAgent
from src.ai.client import AIError
from src.ai.schemas import ThesisSuggestionResult
from src.ai.schemas.thesis_debate import DebateOutput
from src.api.deps import (
    get_current_user_id,
    get_review_service,
    get_symbol_registry,
    get_thesis_debate_agent,
    get_thesis_service,
    get_thesis_suggest_agent,
    get_timeline_service,
)
from src.api.dto.thesis import (
    ApplyRecommendationRequest,
    ApplyAiReviewRequest,
    AssumptionCreateRequest,
    AssumptionListResponse,
    AssumptionResponse,
    AssumptionUpdateRequest,
    CatalystCreateRequest,
    CatalystListResponse,
    CatalystResponse,
    CatalystUpdateRequest,
    DebateRequest,
    HealthScoreBreakdown,
    HealthScoreResponse,
    RecommendationListResponse,
    RecommendationResponse,
    ThesisCreateRequest,
    ThesisListResponse,
    ThesisResponse,
    ThesisReviewListResponse,
    ThesisReviewResponse,
    ThesisUpdateRequest,
)
from src.market.registry import SymbolNotFoundError, SymbolRegistry
from src.readmodel.schemas import ConvictionTimelineResponse
from src.readmodel.timeline_service import ThesisTimelineService
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
    sym_registry: SymbolRegistry = Depends(get_symbol_registry),
) -> ThesisSuggestionResult:
    """Ask AI to draft an investment thesis for a ticker.

    Resolves ticker → company_name + sector + key_metrics via SymbolRegistry
    (market segment) before calling the AI agent, so the model has correct
    Vietnamese market context instead of falling back on global training data.

    Returns a ThesisSuggestionResult — a *draft* for the investor to review.
    Nothing is persisted. The investor must confirm and call POST /thesis to save.

    Price hints (entry_price_hint, target_price_hint, stop_loss_hint) are
    AI estimates only — do NOT auto-save without user confirmation.
    """
    # --- Resolve VN market context from registry (market segment) ---
    company_name = ""
    sector = ""
    extra_context = ""
    try:
        info = sym_registry.resolve(ticker)
        company_name = info.name
        sector = info.sector.value
        if info.key_metrics:
            extra_context = f"Key metrics cần theo dõi: {info.key_metrics}"
    except SymbolNotFoundError:
        # Graceful fallback: ticker không có trong registry → AI tự suy luận
        pass

    try:
        return await agent.suggest(
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            extra_context=extra_context,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"AI response could not be parsed: {exc}",
        ) from exc
    except AIError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI suggest failed: {exc}",
        ) from exc


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
    """
    catalyst_inputs: list[AddCatalystInput] = [
        AddCatalystInput(description=desc) for desc in (body.catalysts or [])
    ]

    thesis = await svc.create(
        user_id,
        CreateThesisInput(
            user_id=user_id,
            ticker=body.ticker,
            title=body.title,
            summary=body.summary,
            direction=body.direction,
            entry_price=body.entry_price,
            target_price=body.target_price,
            stop_loss=body.stop_loss,
        ),
    )

    # Save assumptions (list[str] → AddAssumptionInput)
    for desc in (body.assumptions or []):
        await svc.add_assumption(
            thesis.id, user_id, AddAssumptionInput(description=desc)
        )

    # Save catalysts (already mapped to AddCatalystInput above)
    for cat in catalyst_inputs:
        await svc.add_catalyst(thesis.id, user_id, cat)

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
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status: {status_filter}. Valid: {[s.value for s in ThesisStatus]}",
            ) from exc
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
        raise _not_found(exc) from exc

    scoring = ScoringService()
    total, breakdown = scoring.compute_with_breakdown(thesis)
    tier_label, tier_icon = score_tier(total)

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
    """Partially update thesis header fields (title, summary, direction, prices)."""
    try:
        thesis = await svc.update(
            thesis_id,
            user_id,
            UpdateThesisInput(
                title=body.title,
                summary=body.summary,
                direction=body.direction,
                entry_price=body.entry_price,
                target_price=body.target_price,
                stop_loss=body.stop_loss,
            ),
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc) from exc
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc) from exc
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
        raise _not_found(exc) from exc


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
        raise _not_found(exc) from exc
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc) from exc
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
        raise _not_found(exc) from exc
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc) from exc
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
        raise _not_found(exc) from exc

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
# Conviction Score Timeline
# ---------------------------------------------------------------------------


@router.get("/{thesis_id}/conviction-timeline", response_model=ConvictionTimelineResponse)
async def get_conviction_timeline(
    thesis_id: int,
    limit: int = Query(default=20, ge=2, le=50, description="Số data-point trả về (tối thiểu 2, tối đa 50)"),
    _user_id: str = Depends(get_current_user_id),
    timeline_svc: ThesisTimelineService = Depends(get_timeline_service),
) -> ConvictionTimelineResponse:
    """Trả về cỗ conviction score theo thời gian của một thesis."""
    result = await timeline_svc.get_conviction_timeline(thesis_id=thesis_id, limit=limit)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thesis {thesis_id} not found.",
        )
    return result


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
        raise _not_found(exc) from exc
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
        raise _not_found(exc) from exc
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc) from exc
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
        raise _not_found(exc) from exc
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
        raise _not_found(exc) from exc
    except AssumptionNotFoundError as exc:
        raise _not_found(exc) from exc
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
        raise _not_found(exc) from exc
    except AssumptionNotFoundError as exc:
        raise _not_found(exc) from exc


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
        raise _not_found(exc) from exc
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
        raise _not_found(exc) from exc
    except ThesisAlreadyClosedError as exc:
        raise _conflict(exc) from exc
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
        raise _not_found(exc) from exc
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
        raise _not_found(exc) from exc
    except CatalystNotFoundError as exc:
        raise _not_found(exc) from exc
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
        raise _not_found(exc) from exc
    except CatalystNotFoundError as exc:
        raise _not_found(exc) from exc


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
        raise _not_found(exc) from exc
    except ReviewNotAllowedError as exc:
        raise _conflict(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI review failed: {exc}",
        ) from exc
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
        raise _not_found(exc) from exc
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
        reviews = await review_svc.list_reviews(thesis_id=thesis_id, user_id=user_id, limit=1)
    except ThesisNotFoundError as exc:
        raise _not_found(exc) from exc
    if not reviews:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No reviews found for thesis {thesis_id}.",
        )
    return ThesisReviewResponse.model_validate(reviews[0])


# ---------------------------------------------------------------------------
# AI Recommendations — Wave 1
# ---------------------------------------------------------------------------


@router.get("/{thesis_id}/recommendations", response_model=RecommendationListResponse)
async def list_recommendations(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    review_svc: ReviewService = Depends(get_review_service),
) -> RecommendationListResponse:
    """Trả danh sách AI recommendations đang PENDING cho một thesis."""
    try:
        recs = await review_svc.list_pending_recommendations(thesis_id=thesis_id, user_id=user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc) from exc
    return RecommendationListResponse(
        thesis_id=thesis_id,
        items=[RecommendationResponse.model_validate(r) for r in recs],
        total=len(recs),
    )


@router.post(
    "/{thesis_id}/recommendations/{recommendation_id}/apply",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def apply_recommendation(
    thesis_id: int,
    recommendation_id: int,
    body: ApplyRecommendationRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
) -> None:
    """Accept hoặc reject một AI recommendation."""
    try:
        await svc.apply_recommendation(
            thesis_id=thesis_id,
            recommendation_id=recommendation_id,
            user_id=user_id,
            accept=(body.action == "accept"),
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{thesis_id}/ai-review/apply",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def apply_ai_review_bulk(
    thesis_id: int,
    body: ApplyAiReviewRequest,
    user_id: str = Depends(get_current_user_id),
    review_svc: ReviewService = Depends(get_review_service),
) -> None:
    """Áp dụng nhiều AI recommendations cùng lúc cho một thesis."""
    try:
        await review_svc.apply_bulk_recommendations(
            thesis_id=thesis_id,
            user_id=user_id,
            applied_recommendation_ids=body.applied_recommendation_ids,
            verdict=body.verdict,
            ai_confidence=body.ai_confidence,
        )
    except ThesisNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# AI Debate — Wave C.2
# ---------------------------------------------------------------------------


@router.post("/{thesis_id}/debate", response_model=DebateOutput)
async def debate_thesis(
    thesis_id: int,
    body: DebateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: ThesisService = Depends(get_thesis_service),
    agent: ThesisDebateAgent = Depends(get_thesis_debate_agent),  # type: ignore[type-arg]
) -> DebateOutput:
    """Trigger AI debate (devil's advocate) for a thesis.

    User-initiated, deep adversarial analysis. Does NOT persist to DB —
    fire-and-return per ThesisDebateAgent contract.

    debate_focus narrows the analysis:
      - "entry"  → challenge entry timing and valuation
      - "exit"   → challenge exit/target price assumptions
      - "sizing" → challenge position sizing vs conviction and risk
      - null     → full debate across all dimensions

    Returns DebateOutput with challenges sorted CRITICAL → MINOR.
    On AI failure: returns fallback with empty challenges and confidence=0.0.
    """
    try:
        thesis = await svc.get(thesis_id, user_id)
    except ThesisNotFoundError as exc:
        raise _not_found(exc) from exc

    assumptions = [
        {"id": a.id, "description": a.description, "status": a.status.value}
        for a in (thesis.assumptions or [])
    ]
    catalysts = [
        {"id": c.id, "description": c.description, "status": c.status.value}
        for c in (thesis.catalysts or [])
    ]
    # Derive invalidation conditions from explicitly invalid assumptions
    invalidation_conditions = [
        a.description
        for a in (thesis.assumptions or [])
        if a.status.value == "invalid"
    ]
    days_since = (datetime.now(UTC) - thesis.created_at).days

    return await agent.run(
        thesis_id=thesis_id,
        ticker=thesis.ticker,
        thesis_title=thesis.title,
        thesis_summary=thesis.summary or "",
        assumptions=assumptions,
        catalysts=catalysts,
        invalidation_conditions=invalidation_conditions,
        days_since_written=days_since,
        debate_focus=body.debate_focus,
        user_id=user_id,
    )
