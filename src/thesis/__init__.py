"""Thesis segment — thesis lifecycle, assumptions, catalysts, scoring, invalidation.

Public API:
    Models:       Thesis, Assumption, Catalyst, ThesisReview, ThesisSnapshot
    Enums:        ThesisStatus, AssumptionStatus, CatalystStatus, ReviewVerdict
    Repository:   ThesisRepository
    Services:     ThesisService, ScoringService, InvalidationService
    Listeners:    ThesisReviewListener  (Wave 6 — event-driven review loop)
                  SignalReviewTriggerListener  (Wave C — SignalEngine → ThesisReview bridge)
    DTOs:         CreateThesisInput, UpdateThesisInput
    Errors:       ThesisNotFoundError, ThesisAlreadyClosedError
    Results:      InvalidationCheckResult
    Queries:      TickerDirectionQuery  (cross-segment read contract for watchlist)
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
from src.thesis.signal_review_trigger_listener import SignalReviewTriggerListener
from src.thesis.thesis_review_listener import ThesisReviewListener
from src.thesis.ticker_direction_query import TickerDirectionQuery

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
    "SignalReviewTriggerListener",
    # DTOs
    "CreateThesisInput",
    "UpdateThesisInput",
    # Errors
    "ThesisNotFoundError",
    "ThesisAlreadyClosedError",
    # Results
    "InvalidationCheckResult",
    # Queries
    "TickerDirectionQuery",
]
