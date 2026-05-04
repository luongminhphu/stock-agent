"""Decision & Lesson API routes.

Owner: api segment.
Exposes DecisionService and LessonService capabilities over HTTP.

Route group: /api/v1/decisions  and  /api/v1/lessons

Endpoints:
    POST   /decisions/              — log a new trade decision
    GET    /decisions/              — list decisions (optional ?evaluated_only=true)
    POST   /decisions/{id}/evaluate — evaluate realized outcome (no AI)
    GET    /decisions/{id}/replay   — run AI replay analysis
    GET    /lessons/                — list lesson snippets (optional ?ticker=VCB)

All routes are scoped to the authenticated owner via get_current_user_id.
No business logic here — thin adapter delegating to domain services.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db, get_decision_service, get_lesson_service

router = APIRouter(tags=["decisions"])


# ---------------------------------------------------------------------------
# Request / Response DTOs
# ---------------------------------------------------------------------------

class LogDecisionRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    decision_type: str = Field(..., description="BUY | SELL | HOLD | SKIP")
    thesis_id: int | None = Field(None, description="Optional linked thesis")
    price_at_decision: float = Field(..., gt=0)
    thesis_score_at_decision: float | None = Field(None, ge=0, le=100)
    rationale: str | None = Field(None, max_length=2000)
    review_horizon_days: int = Field(30, ge=1, le=365)


class DecisionResponse(BaseModel):
    id: int
    user_id: str
    ticker: str
    decision_type: str
    price_at_decision: float
    thesis_score_at_decision: float | None
    rationale: str | None
    review_horizon_days: int
    outcome_pnl_pct: float | None
    outcome_verdict: str | None
    key_lesson: str | None
    pattern_detected: str | None
    decision_at: str
    outcome_evaluated_at: str | None

    @classmethod
    def from_orm(cls, d: object) -> "DecisionResponse":
        return cls(
            id=d.id,  # type: ignore[attr-defined]
            user_id=d.user_id,  # type: ignore[attr-defined]
            ticker=d.ticker,  # type: ignore[attr-defined]
            decision_type=d.decision_type,  # type: ignore[attr-defined]
            price_at_decision=d.price_at_decision,  # type: ignore[attr-defined]
            thesis_score_at_decision=d.thesis_score_at_decision,  # type: ignore[attr-defined]
            rationale=d.rationale,  # type: ignore[attr-defined]
            review_horizon_days=d.review_horizon_days,  # type: ignore[attr-defined]
            outcome_pnl_pct=d.outcome_pnl_pct,  # type: ignore[attr-defined]
            outcome_verdict=str(d.outcome_verdict) if d.outcome_verdict else None,  # type: ignore[attr-defined]
            key_lesson=d.key_lesson,  # type: ignore[attr-defined]
            pattern_detected=d.pattern_detected,  # type: ignore[attr-defined]
            decision_at=d.decision_at.isoformat(),  # type: ignore[attr-defined]
            outcome_evaluated_at=(
                d.outcome_evaluated_at.isoformat()  # type: ignore[attr-defined]
                if d.outcome_evaluated_at  # type: ignore[attr-defined]
                else None
            ),
        )


class ReplayResponse(BaseModel):
    decision_id: int
    outcome_verdict: str | None
    outcome_pnl_pct: float | None
    what_went_right: list[str]
    what_went_wrong: list[str]
    key_lesson: str | None
    pattern_detected: str | None
    suggested_adjustment: str | None
    confidence: float


class LessonSnippetResponse(BaseModel):
    decision_id: int
    ticker: str
    decision_type: str
    outcome_verdict: str | None
    key_lesson: str
    pattern_detected: str | None
    decision_at: str


# ---------------------------------------------------------------------------
# Decision endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/decisions",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log a new trade decision",
)
async def log_decision(
    body: LogDecisionRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    svc=Depends(get_decision_service),
) -> DecisionResponse:
    """Log a trade decision for future outcome evaluation and replay."""
    decision = await svc.log_decision(
        user_id=user_id,
        ticker=body.ticker.upper().strip(),
        decision_type=body.decision_type.upper().strip(),
        thesis_id=body.thesis_id,
        price_at_decision=body.price_at_decision,
        thesis_score_at_decision=body.thesis_score_at_decision,
        rationale=body.rationale,
        review_horizon_days=body.review_horizon_days,
    )
    return DecisionResponse.from_orm(decision)


@router.get(
    "/decisions",
    response_model=list[DecisionResponse],
    summary="List trade decisions",
)
async def list_decisions(
    user_id: Annotated[str, Depends(get_current_user_id)],
    evaluated_only: bool = Query(False, description="Return only evaluated decisions"),
    ticker: str | None = Query(None, description="Filter by ticker"),
    limit: int = Query(50, ge=1, le=200),
    svc=Depends(get_decision_service),
) -> list[DecisionResponse]:
    """List trade decisions for the current user."""
    decisions = await svc.list_decisions(
        user_id=user_id,
        evaluated_only=evaluated_only,
        ticker=ticker.upper().strip() if ticker else None,
        limit=limit,
    )
    return [DecisionResponse.from_orm(d) for d in decisions]


@router.post(
    "/decisions/{decision_id}/evaluate",
    response_model=DecisionResponse,
    summary="Evaluate realized outcome for a decision",
)
async def evaluate_decision(
    decision_id: int,
    user_id: Annotated[str, Depends(get_current_user_id)],
    svc=Depends(get_decision_service),
) -> DecisionResponse:
    """Compute realized PnL and assign CORRECT/INCORRECT/MIXED verdict.
    No AI call — pure price comparison.
    """
    try:
        decision = await svc.evaluate_outcome(decision_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DecisionResponse.from_orm(decision)


@router.get(
    "/decisions/{decision_id}/replay",
    response_model=ReplayResponse,
    summary="Run AI replay analysis for a decision",
)
async def replay_decision(
    decision_id: int,
    user_id: Annotated[str, Depends(get_current_user_id)],
    svc=Depends(get_decision_service),
) -> ReplayResponse:
    """Run ReplayAgent on an evaluated decision to extract key_lesson and pattern.
    Persists lesson back to DecisionLog automatically.
    """
    try:
        envelope = await svc.analyze_decision(decision_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    r = envelope.replay
    d = envelope.decision
    return ReplayResponse(
        decision_id=d.id,
        outcome_verdict=str(d.outcome_verdict) if d.outcome_verdict else None,
        outcome_pnl_pct=d.outcome_pnl_pct,
        what_went_right=getattr(r, "what_went_right", []) or [],
        what_went_wrong=getattr(r, "what_went_wrong", []) or [],
        key_lesson=getattr(r, "key_lesson", None),
        pattern_detected=getattr(r, "pattern_detected", None),
        suggested_adjustment=getattr(r, "suggested_adjustment", None),
        confidence=getattr(r, "confidence", 0.0),
    )


# ---------------------------------------------------------------------------
# Lesson endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/lessons",
    response_model=list[LessonSnippetResponse],
    summary="List persisted AI lessons",
)
async def list_lessons(
    user_id: Annotated[str, Depends(get_current_user_id)],
    ticker: str | None = Query(None, description="Filter to a specific ticker"),
    limit: int = Query(10, ge=1, le=50),
    lookback_days: int = Query(90, ge=1, le=365),
    lesson_svc=Depends(get_lesson_service),
) -> list[LessonSnippetResponse]:
    """Return recent AI lessons from the Decision Replay loop.
    Used by dashboard and debugging tools.
    """
    snippets = await lesson_svc.get_recent_lessons(
        user_id,
        ticker=ticker.upper().strip() if ticker else None,
        max_lessons=limit,
        lookback_days=lookback_days,
    )
    return [
        LessonSnippetResponse(
            decision_id=s.decision_id,
            ticker=s.ticker,
            decision_type=s.decision_type,
            outcome_verdict=s.outcome_verdict,
            key_lesson=s.key_lesson,
            pattern_detected=s.pattern_detected,
            decision_at=s.decision_at,
        )
        for s in snippets
    ]
