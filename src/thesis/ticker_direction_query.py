"""TickerDirectionQuery — thin read contract for cross-segment thesis direction lookup.

Owner: thesis segment.

Purpose:
    Expose active thesis direction (BULLISH/BEARISH) per ticker + user as a
    simple {ticker: 'bull'|'bear'} dict.

    This file is the ONLY approved cross-segment interface for watchlist →
    thesis reads. ScanService receives this class via constructor injection
    (same pattern as credibility_agent) — it does NOT import thesis.models
    directly.

Query contract:
    - Returns only ACTIVE theses
    - NEUTRAL direction is excluded (not actionable for divergence detection)
    - One query per call (bulk over tickers list — no N+1)
    - Caller (ScanService) owns the session lifecycle
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thesis.models import Thesis, ThesisDirection, ThesisStatus


class TickerDirectionQuery:
    """Single-method read query: active thesis direction by ticker + user.

    Injected into ScanService as `ticker_direction_query` to enable
    THESIS_DIVERGENCE signal enrichment without cross-segment model imports.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_direction_map(
        self, user_id: str, tickers: list[str]
    ) -> dict[str, str]:
        """Return {ticker: 'bull'|'bear'} for tickers that have an active BULLISH/BEARISH thesis.

        Tickers with no active thesis, or with NEUTRAL direction, are omitted.
        ScanService uses the absence of a key as "no thesis context" — engine skips.

        Args:
            user_id: Scope query to this user.
            tickers: List of tickers to check (typically all tickers in watchlist).

        Returns:
            Dict mapping ticker → 'bull' (BULLISH) or 'bear' (BEARISH).
            Empty dict when no matching theses found.
        """
        if not tickers:
            return {}

        stmt = (
            select(Thesis.ticker, Thesis.direction)
            .where(Thesis.user_id == user_id)
            .where(Thesis.ticker.in_([t.upper() for t in tickers]))
            .where(Thesis.status == ThesisStatus.ACTIVE)
            .where(Thesis.direction.is_not(None))
            .where(Thesis.direction != ThesisDirection.NEUTRAL)
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        mapping: dict[str, str] = {}
        for ticker, direction in rows:
            if direction == ThesisDirection.BULLISH:
                mapping[ticker] = "bull"
            elif direction == ThesisDirection.BEARISH:
                mapping[ticker] = "bear"
        return mapping
