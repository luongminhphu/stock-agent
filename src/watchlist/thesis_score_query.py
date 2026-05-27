"""ThesisScoreQuery — thin read adapter for ScanService.

Owner: watchlist segment.
Consumer: ScanService (same session, injected at construction).

Returns a dict[ticker, float] of health scores (0-100) for all active
theses belonging to a user. Uses ThesisRepository (thesis segment) for
DB reads and ScoringService (thesis segment, pure) for computation.

Design constraints:
  - Accepts an AsyncSession directly (NOT session_factory) because ScanService
    already owns a session — no extra connection needed.
  - Never raises — returns empty dict on any error so ScanService degrades
    gracefully.
  - ScoringService is pure (no DB, no AI) — safe to call in a tight loop.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger

logger = get_logger(__name__)

# Score threshold below which scan sensitivity is increased.
# Matches ScoringService SCORE_TIERS: <=50 → Weak/Critical.
WEAK_THRESHOLD = 50.0
CRITICAL_THRESHOLD = 30.0


class ThesisScoreQuery:
    """Compute thesis health scores for a list of tickers.

    Usage (inside ScanService.scan_user):
        query = ThesisScoreQuery(session)
        score_map = await query.get_score_map(user_id, tickers)
        # score_map[ticker] → float 0-100, or absent if no active thesis
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_score_map(
        self, user_id: str, tickers: list[str]
    ) -> dict[str, float]:
        """Return {ticker: health_score} for tickers with an active thesis.

        Tickers without an active thesis are absent from the returned dict
        (caller treats absence as "no score context" — no sensitivity change).
        """
        if not tickers:
            return {}

        try:
            from src.thesis.repository import ThesisRepository
            from src.thesis.scoring_service import ScoringService

            repo = ThesisRepository(self._session)
            scoring = ScoringService()

            # list_active_for_user eager-loads assumptions + catalysts + reviews
            # — all needed by ScoringService.compute().
            theses = await repo.list_active_for_user(user_id=user_id)

            ticker_set = set(tickers)
            score_map: dict[str, float] = {}
            for thesis in theses:
                if thesis.ticker not in ticker_set:
                    continue
                try:
                    score = scoring.compute(thesis)
                    score_map[thesis.ticker] = score
                except Exception as exc:
                    logger.warning(
                        "thesis_score_query.score_failed",
                        ticker=thesis.ticker,
                        thesis_id=thesis.id,
                        error=str(exc),
                    )

            logger.debug(
                "thesis_score_query.done",
                user_id=user_id,
                requested=len(tickers),
                scored=len(score_map),
            )
            return score_map

        except Exception as exc:
            logger.warning(
                "thesis_score_query.failed",
                user_id=user_id,
                error=str(exc),
            )
            return {}
