"""Thesis routes — review endpoints.

Owner: api segment.
No business logic here — delegates entirely to ReviewService.

Endpoints:
    POST /thesis/{thesis_id}/review         — trigger AI review
    GET  /thesis/{thesis_id}/reviews        — list past reviews
    GET  /thesis/{thesis_id}/reviews/latest — latest review only
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user_id, get_review_service
from src.api.dto.thesis import ThesisReviewListResponse, ThesisReviewResponse
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.service import ThesisNotFoundError

router = APIRouter(prefix="/thesis", tags=["thesis"])


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
    """Trigger an AI review for a thesis. Returns the new review record."""
    try:
        review = await review_svc.review_thesis(
            thesis_id=thesis_id,
            user_id=user_id,
        )
    except ThesisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ReviewNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        # AI parse failure
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
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
            thesis_id=thesis_id,
            user_id=user_id,
            limit=min(limit, 50),
        )
    except ThesisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

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
    """Return only the most recent AI review for a thesis."""
    try:
        reviews = await review_svc.list_reviews(
            thesis_id=thesis_id,
            user_id=user_id,
            limit=1,
        )
    except ThesisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if not reviews:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No reviews found for thesis {thesis_id}.",
        )
    return ThesisReviewResponse.model_validate(reviews[0])
