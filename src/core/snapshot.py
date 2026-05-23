"""SystemSnapshotBuilder — thu thập cross-segment state.

Owner: core segment.
Reads from: watchlist, thesis, readmodel (scan), portfolio.
All sources are fetched concurrently via asyncio.gather.
Partial failures are swallowed — a missing source returns an empty list,
not an exception, so the snapshot is always complete enough to be useful.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import (
    MarketSignal,
    PortfolioContext,
    SystemSnapshot,
    ThesisRef,
    WatchlistAlert,
)


class SystemSnapshotBuilder:
    """Collect a full SystemSnapshot for a given user.

    Usage::

        snapshot = await SystemSnapshotBuilder(session, user_id).build()
    """

    OVERDUE_DAYS = 14  # thesis với AI review cũ hơn N ngày coi là overdue

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def build(self) -> SystemSnapshot:
        alerts, thesis_due, market_sigs, portfolio = await asyncio.gather(
            self._fetch_alerts(),
            self._fetch_overdue_thesis(),
            self._fetch_market_signals(),
            self._fetch_portfolio(),
            return_exceptions=True,
        )
        return SystemSnapshot(
            watchlist_alerts=alerts if isinstance(alerts, list) else [],
            thesis_due_review=thesis_due if isinstance(thesis_due, list) else [],
            market_anomalies=market_sigs if isinstance(market_sigs, list) else [],
            portfolio_context=(
                portfolio
                if isinstance(portfolio, PortfolioContext)
                else PortfolioContext()
            ),
            timestamp=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Private sources
    # ------------------------------------------------------------------

    async def _fetch_alerts(self) -> list[WatchlistAlert]:
        """Alerts đã trigger, chưa được dismiss."""
        try:
            from src.watchlist.models import Alert  # type: ignore[import]

            rows = (
                await self.session.execute(
                    select(Alert)
                    .where(
                        Alert.user_id == self.user_id,
                        Alert.triggered_at.isnot(None),
                        Alert.dismissed_at.is_(None),
                    )
                    .order_by(Alert.triggered_at.desc())
                    .limit(20)
                )
            ).scalars().all()
            return [
                WatchlistAlert(
                    ticker=r.ticker,
                    alert_type=r.alert_type,
                    triggered_at=r.triggered_at,
                    note=getattr(r, "note", None),
                )
                for r in rows
            ]
        except Exception:
            return []

    async def _fetch_overdue_thesis(self) -> list[ThesisRef]:
        """Active theses chưa có AI review trong OVERDUE_DAYS ngày."""
        try:
            from src.thesis.models import Thesis, ThesisReview  # type: ignore[import]

            cutoff = datetime.now(timezone.utc) - timedelta(days=self.OVERDUE_DAYS)
            theses = (
                await self.session.execute(
                    select(Thesis).where(
                        Thesis.user_id == self.user_id,
                        Thesis.status == "active",
                    )
                )
            ).scalars().all()

            result: list[ThesisRef] = []
            for t in theses:
                last_review = (
                    await self.session.execute(
                        select(ThesisReview.created_at)
                        .where(ThesisReview.thesis_id == t.id)
                        .order_by(ThesisReview.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

                if last_review is None or last_review.replace(tzinfo=timezone.utc) < cutoff:
                    days_overdue = (
                        (datetime.now(timezone.utc) - last_review.replace(tzinfo=timezone.utc)).days
                        if last_review
                        else 999
                    )
                    result.append(
                        ThesisRef(
                            thesis_id=t.id,
                            ticker=t.ticker,
                            last_reviewed_at=last_review,
                            days_overdue=days_overdue,
                        )
                    )
            return result
        except Exception:
            return []

    async def _fetch_market_signals(self) -> list[MarketSignal]:
        """Lấy alerts từ scan snapshot gần nhất (delegate sang DashboardService)."""
        try:
            from src.readmodel.dashboard_service import DashboardService  # type: ignore[import]

            svc = DashboardService(self.session)
            snap = await svc.get_scan_latest(self.user_id)
            if not snap:
                return []
            alerts = snap.get("alerts", [])
            return [
                MarketSignal(
                    ticker=a.get("ticker", "?"),
                    signal_type=a.get("type", "scan"),
                    note=a.get("note"),
                )
                for a in alerts[:10]
            ]
        except Exception:
            return []

    async def _fetch_portfolio(self) -> PortfolioContext:
        """Tổng hợp open positions."""
        try:
            from src.portfolio.models import Position  # type: ignore[import]

            rows = (
                await self.session.execute(
                    select(Position.ticker).where(
                        Position.user_id == self.user_id,
                        Position.closed_at.is_(None),
                        Position.qty > 0,
                    )
                )
            ).scalars().all()
            return PortfolioContext(
                total_positions=len(rows),
                top_exposed_tickers=list(rows[:5]),
            )
        except Exception:
            return PortfolioContext()
