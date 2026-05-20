"""OutcomeFillerService — fill missing outcomes for closed DecisionLogs.

Owner: thesis segment.
Called by OutcomeFillerScheduler after market close (15:05 ICT).

Flow:
    - Find DecisionLogs where outcome_pnl_pct IS NULL
      AND (now - decision_at).days >= review_horizon_days  (due for evaluation)
    - Fetch current price via QuoteService
    - Compute outcome_pnl_pct = (current_price - price_at_decision) / price_at_decision * 100
    - Set outcome_price, outcome_evaluated_at, outcome_verdict
    - Persist, return count of records filled

No AI calls — pure data enrichment.
Segment boundary: imports only thesis.models and market.quote_service (via DI).
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import DecisionLog, DecisionType, OutcomeVerdict

logger = get_logger(__name__)

# Threshold (%) to classify CORRECT / INCORRECT vs MIXED
_VERDICT_THRESHOLD_PCT = 5.0


class OutcomeFillerService:
    """Fill outcome fields on DecisionLogs whose review horizon has elapsed.

    Args:
        session:       AsyncSession — caller owns lifecycle + commit.
        quote_service: QuoteService instance (injected, not imported).
    """

    def __init__(self, session: AsyncSession, quote_service) -> None:
        self._session = session
        self._quote_service = quote_service

    async def fill_pending_outcomes(self, user_id: str) -> int:
        """Find due DecisionLogs, fetch price, compute outcome, persist.

        Returns:
            Number of records successfully updated.
        """
        now = datetime.datetime.now(tz=datetime.UTC)

        stmt = select(DecisionLog).where(
            DecisionLog.user_id == user_id,
            DecisionLog.outcome_pnl_pct.is_(None),
            DecisionLog.price_at_decision.is_not(None),
        )
        result = await self._session.execute(stmt)
        logs = result.scalars().all()

        # Filter: only logs where review_horizon_days have elapsed
        due = [
            log for log in logs
            if (now - log.decision_at).days >= log.review_horizon_days
        ]

        if not due:
            logger.info("outcome_filler.no_due_logs", user_id=user_id, total_pending=len(logs))
            return 0

        logger.info(
            "outcome_filler.due_logs_found",
            user_id=user_id,
            due_count=len(due),
            total_pending=len(logs),
        )

        filled = 0
        for log in due:
            try:
                quote = await self._quote_service.get_quote(log.ticker)
                current_price: float = quote.price

                pnl_pct = round(
                    (current_price - log.price_at_decision) / log.price_at_decision * 100, 2
                )
                log.outcome_price = current_price
                log.outcome_pnl_pct = pnl_pct
                log.outcome_evaluated_at = now
                log.outcome_verdict = _classify_verdict(pnl_pct, log.decision_type)
                filled += 1

                logger.info(
                    "outcome_filler.filled",
                    ticker=log.ticker,
                    decision_log_id=log.id,
                    decision_type=log.decision_type,
                    price_at_decision=log.price_at_decision,
                    current_price=current_price,
                    pnl_pct=pnl_pct,
                    verdict=log.outcome_verdict,
                )

            except Exception as exc:
                logger.warning(
                    "outcome_filler.skip",
                    ticker=log.ticker,
                    decision_log_id=log.id,
                    error=str(exc),
                )

        return filled


def _classify_verdict(pnl_pct: float, decision_type: DecisionType) -> OutcomeVerdict:
    """Classify outcome verdict based on pnl_pct and decision direction.

    BUY / ADD  — long side: positive pnl = CORRECT, negative = INCORRECT.
    SELL / REDUCE — short side: negative pnl = CORRECT, positive = INCORRECT.
    HOLD       — direction-neutral: use absolute magnitude.
    Within ±THRESHOLD → MIXED.
    """
    buy_side = decision_type in (DecisionType.BUY, DecisionType.ADD)
    sell_side = decision_type in (DecisionType.SELL, DecisionType.REDUCE)

    if buy_side:
        if pnl_pct >= _VERDICT_THRESHOLD_PCT:
            return OutcomeVerdict.CORRECT
        if pnl_pct <= -_VERDICT_THRESHOLD_PCT:
            return OutcomeVerdict.INCORRECT
    elif sell_side:
        if pnl_pct <= -_VERDICT_THRESHOLD_PCT:
            return OutcomeVerdict.CORRECT
        if pnl_pct >= _VERDICT_THRESHOLD_PCT:
            return OutcomeVerdict.INCORRECT
    else:  # HOLD
        if pnl_pct >= _VERDICT_THRESHOLD_PCT:
            return OutcomeVerdict.CORRECT
        if pnl_pct <= -_VERDICT_THRESHOLD_PCT:
            return OutcomeVerdict.INCORRECT

    return OutcomeVerdict.MIXED
