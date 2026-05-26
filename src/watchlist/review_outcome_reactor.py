"""ReviewOutcomeReactor — watchlist reacts to AI ThesisReview outcomes.

Owner: watchlist segment.
Purpose: After the AI judge produces a ThesisReview, this reactor:
  1. Escalates / de-escalates WatchlistItem.priority based on verdict + risk signals
  2. Updates WatchlistItem.note with a short AI verdict summary
  3. Creates a THESIS_TRIGGER Alert from next_watch_items (deduped per review)

Entry point::

    reactor = ReviewOutcomeReactor(session_factory=AsyncSessionLocal)
    await reactor.react(review_id=review.id)

Caller is responsible for committing the session (or the reactor can be
passed an already-open session via react_in_session for transactional use).

Priority rules:
  BEARISH               → priority = PRIORITY_BEARISH  (10)  urgent
  BULLISH + no risks    → priority = PRIORITY_BULLISH   (90)  de-prioritise
  NEUTRAL/WATCHLIST
    + >=2 risk signals  → priority = PRIORITY_RISKY     (30)  elevated watch
  otherwise             → no priority change

Alert dedup key: 'review_outcome:{review_id}' — idempotent across retries.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.models import (
    Alert,
    AlertConditionType,
    AlertStatus,
    WatchlistItem,
)
from src.watchlist.service import WatchlistItemNotFoundError

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Priority constants
# ---------------------------------------------------------------------------

PRIORITY_BEARISH: int = 10   # urgent — needs immediate attention
PRIORITY_RISKY: int = 30     # elevated — monitor closely
PRIORITY_BULLISH: int = 90   # de-prioritised — thesis looking strong

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_text_list(raw: str | None) -> list[str]:
    """Parse newline-delimited or JSON-array Text column into list[str]."""
    if not raw:
        return []
    import json
    stripped = raw.strip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except (json.JSONDecodeError, ValueError):
            pass
    return [line.strip() for line in stripped.splitlines() if line.strip()]


def _compute_priority_change(
    verdict: str,
    risk_signals: list[str],
) -> int | None:
    """Return new priority value or None if no change needed."""
    v = verdict.upper()
    if v == "BEARISH":
        return PRIORITY_BEARISH
    if v == "BULLISH" and len(risk_signals) == 0:
        return PRIORITY_BULLISH
    if v in ("NEUTRAL", "WATCHLIST") and len(risk_signals) >= 2:
        return PRIORITY_RISKY
    return None


def _build_note(verdict: str, confidence: float, summary: str | None, reasoning: str | None) -> str:
    """Build a short watchlist note from AI review output."""
    confidence_pct = round(confidence * 100)
    base = summary or (reasoning[:100] + "...") if reasoning and len(reasoning) > 100 else (reasoning or "")
    return f"[{verdict} {confidence_pct}%] {base}".strip()


# ---------------------------------------------------------------------------
# Reactor
# ---------------------------------------------------------------------------


class ReviewOutcomeReactor:
    """Mutates WatchlistItem and creates THESIS_TRIGGER alerts after a ThesisReview.

    Stateless — safe to instantiate per review or as a singleton.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def react(self, review_id: int) -> None:
        """Load review, resolve watchlist item, apply all mutations, commit.

        Args:
            review_id: PK of the ThesisReview record to react to.

        Silently skips if:
        - review_id does not exist
        - ticker is not in the user's watchlist
        """
        async with self._session_factory() as session:
            await self._react_in_session(session, review_id)
            await session.commit()

    async def react_in_session(self, session: AsyncSession, review_id: int) -> None:
        """Same as react() but uses an existing session — caller must commit.

        Use this when the review creation and watchlist mutation should be
        part of the same transaction.
        """
        await self._react_in_session(session, review_id)

    # ── internal ────────────────────────────────────────────────────────────

    async def _react_in_session(self, session: AsyncSession, review_id: int) -> None:
        # Import thesis models locally to respect segment boundary
        from src.thesis.models import Thesis, ThesisReview  # noqa: PLC0415

        # 1. Load ThesisReview + Thesis (single joined query)
        stmt = (
            select(ThesisReview, Thesis)
            .join(Thesis, ThesisReview.thesis_id == Thesis.id)
            .where(ThesisReview.id == review_id)
        )
        result = await session.execute(stmt)
        row = result.first()
        if row is None:
            logger.warning("review_outcome_reactor.review_not_found", review_id=review_id)
            return

        review, thesis = row

        # 2. Load WatchlistItem for this user + ticker
        item_stmt = select(WatchlistItem).where(
            WatchlistItem.user_id == thesis.user_id,
            WatchlistItem.ticker == thesis.ticker,
        )
        item_result = await session.execute(item_stmt)
        item = item_result.scalar_one_or_none()

        if item is None:
            logger.info(
                "review_outcome_reactor.ticker_not_in_watchlist",
                review_id=review_id,
                ticker=thesis.ticker,
                user_id=thesis.user_id,
            )
            return

        verdict = review.verdict.value if hasattr(review.verdict, "value") else str(review.verdict)
        risk_signals = _parse_text_list(review.risk_signals)
        next_watch_items = _parse_text_list(review.next_watch_items)
        dedup_key = f"review_outcome:{review_id}"

        # 3. Priority escalation
        new_priority = _compute_priority_change(verdict, risk_signals)
        if new_priority is not None and item.priority != new_priority:
            old_priority = item.priority
            item.priority = new_priority
            logger.info(
                "review_outcome_reactor.priority_updated",
                review_id=review_id,
                ticker=thesis.ticker,
                old_priority=old_priority,
                new_priority=new_priority,
                verdict=verdict,
            )

        # 4. Note update
        new_note = _build_note(
            verdict=verdict,
            confidence=review.confidence,
            summary=review.summary,
            reasoning=review.reasoning,
        )
        item.note = new_note
        logger.info(
            "review_outcome_reactor.note_updated",
            review_id=review_id,
            ticker=thesis.ticker,
            note_preview=new_note[:60],
        )

        # 5. THESIS_TRIGGER alert (deduped)
        if next_watch_items:
            await self._ensure_alert(
                session=session,
                review=review,
                thesis=thesis,
                next_watch_items=next_watch_items,
                dedup_key=dedup_key,
                item=item,
                verdict=verdict,
            )

    async def _ensure_alert(
        self,
        session: AsyncSession,
        review: Any,
        thesis: Any,
        next_watch_items: list[str],
        dedup_key: str,
        item: WatchlistItem,
        verdict: str,
    ) -> None:
        """Create a THESIS_TRIGGER alert if dedup_key not already present."""
        # Check existing alert with same dedup_key
        existing_stmt = select(Alert).where(
            Alert.user_id == thesis.user_id,
            Alert.dedup_key == dedup_key,
        )
        existing_result = await session.execute(existing_stmt)
        if existing_result.scalar_one_or_none() is not None:
            logger.debug(
                "review_outcome_reactor.alert_already_exists",
                dedup_key=dedup_key,
            )
            return

        label = "\n".join(f"• {w}" for w in next_watch_items)
        priority_str = "HIGH" if verdict == "BEARISH" else "MEDIUM"

        alert = Alert(
            user_id=thesis.user_id,
            ticker=thesis.ticker,
            watchlist_item_id=item.id,
            condition_type=AlertConditionType.THESIS_TRIGGER,
            threshold=0.0,
            status=AlertStatus.ACTIVE,
            label=label,
            thesis_id=str(thesis.id),
            dedup_key=dedup_key,
            source_event_id=str(review.id),
            priority=priority_str,
            note=f"AI review #{review.id} — {verdict} ({round(review.confidence * 100)}%)",
            created_at=datetime.now(tz=UTC),
        )
        session.add(alert)
        logger.info(
            "review_outcome_reactor.alert_created",
            review_id=review.id,
            ticker=thesis.ticker,
            verdict=verdict,
            watch_items=len(next_watch_items),
            dedup_key=dedup_key,
        )
