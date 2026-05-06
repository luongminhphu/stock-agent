"""Thesis segment — thesis lifecycle, assumptions, catalysts, scoring, invalidation.

Public API:
    Models:       Thesis, Assumption, Catalyst, ThesisReview, ThesisSnapshot
    Enums:        ThesisStatus, AssumptionStatus, CatalystStatus, ReviewVerdict
    Repository:   ThesisRepository
    Services:     ThesisService, ScoringService, InvalidationService
    Listeners:    ThesisReviewListener  (Wave 6 — event-driven review loop)
    DTOs:         CreateThesisInput, UpdateThesisInput
    Errors:       ThesisNotFoundError, ThesisAlreadyClosedError
    Results:      InvalidationCheckResult
"""

from src.thesis.invalidation_service import InvalidationCheckResult, InvalidationService
from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    ReviewVerdict,
    Thesis,
    ThesisReview,
    ThesisSnapshot,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.thesis.scoring_service import ScoringService
from src.thesis.service import (
    CreateThesisInput,
    ThesisAlreadyClosedError,
    ThesisNotFoundError,
    ThesisService,
    UpdateThesisInput,
)
from src.thesis.thesis_review_listener import ThesisReviewListener

__all__ = [
    # Models
    "Thesis",
    "Assumption",
    "Catalyst",
    "ThesisReview",
    "ThesisSnapshot",
    # Enums
    "ThesisStatus",
    "AssumptionStatus",
    "CatalystStatus",
    "ReviewVerdict",
    # Repository
    "ThesisRepository",
    # Services
    "ThesisService",
    "ScoringService",
    "InvalidationService",
    # Listeners
    "ThesisReviewListener",
    # DTOs
    "CreateThesisInput",
    "UpdateThesisInput",
    # Errors
    "ThesisNotFoundError",
    "ThesisAlreadyClosedError",
    # Results
    "InvalidationCheckResult",
]
