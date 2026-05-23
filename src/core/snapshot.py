"""SystemSnapshotBuilder — thu thập cross-segment state.

Owner: core segment.
Reads from: watchlist, thesis, readmodel (scan), portfolio.
All sources are fetched concurrently via asyncio.gather.
Partial failures are swallowed — a missing source returns a safe default,
not an exception, so the snapshot is always complete enough to be useful.

Populates BOTH:
  - Nested sub-models (watchlist, thesis, market, portfolio)
    → consumed by signals.rank_signals()
  - Flat legacy lists (watchlist_alerts, thesis_due_review, market_anomalies)
    → consumed by engine._derive_action() and briefing
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import (
    MarketContext,
    MarketSignal,
    PortfolioContext,
    SystemSnapshot,
    ThesisContext,
    ThesisRef,
    WatchlistAlert,
    WatchlistContext,
)


class SystemSnapshotBuilder:
    """Collect a full SystemSnapshot for a given user.

    Usage::

        snapshot = await SystemSnapshotBuilder(session, user_id).build()
    """

    OVERDUE_DAYS = 14  # thesis older than N days without AI review = overdue
    STALE_DAYS = 3    # thesis without any review in N days = stale

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def build(self) -> SystemSnapshot:
        alerts_flat, thesis_flat, market_flat, portfolio_ctx = await asyncio.gather(
            self._fetch_alerts(),
            self._fetch_overdue_thesis(),
            self._fetch_market_signals(),
            self._fetch_portfolio(),
            return_exceptions=True,
        )

        # Safe defaults on partial failure
        alerts_flat = alerts_flat if isinstance(alerts_flat, list) else []
        thesis_flat = thesis_flat if isinstance(thesis_flat, list) else []
        market_flat = market_flat if isinstance(market_flat, list) else []
        portfolio_ctx = (
            portfolio_ctx
            if isinstance(portfolio_ctx, PortfolioContext)
            else PortfolioContext()
        )

        # Build nested sub-models from flat data
        watchlist_ctx = WatchlistContext(
            triggered_alert_count=len(alerts_flat),
            top_tickers=list({a.ticker for a in alerts_flat})[:5],
            has_volume_spike=any(
                "volume" in (a.alert_type or "").lower() for a in alerts_flat
            ),
        )

        stale_cutoff = datetime.now(timezone.utc) - timedelta(days=self.STALE_DAYS)
        stale_refs = [
            t for t in thesis_flat
            if t.last_reviewed_at is None
            or t.last_reviewed_at.replace(tzinfo=timezone.utc) < stale_cutoff
        ]
        invalidated_refs = [t for t in thesis_flat if t.days_overdue > 30]
        drift_refs = [
            t for t in thesis_flat
            if 7 < t.days_overdue <= 30
        ]
        thesis_ctx = ThesisContext(
            invalidated_count=len(invalidated_refs),
            drift_detected_count=len(drift_refs),
            stale_count=len(stale_refs),
            stale_tickers=[t.ticker for t in stale_refs[:5]],
        )

        opportunity_sigs = [
            s for s in market_flat if "opportunity" in s.signal_type.lower()
        ]
        trend_sigs = [
            s for s in market_flat if "trend" in s.signal_type.lower()
        ]
        market_ctx = MarketContext(
            trend_shift_count=len(trend_sigs),
            opportunity_count=len(opportunity_sigs),
            top_opportunity_tickers=[s.ticker for s in opportunity_sigs[:5]],
        )

        return SystemSnapshot(
            # nested
            watchlist=watchlist_ctx,
            thesis=thesis_ctx,
            market=market_ctx,
            portfolio=portfolio_ctx,
            # flat legacy
            watchlist_alerts=alerts_flat,
            thesis_due_review=thesis_flat,
            market_anomalies=market_flat,
            portfolio_context=portfolio_ctx,
            timestamp=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Private fetchers — each returns a safe default on any exception
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
                        (
                            datetime.now(timezone.utc)
                            - last_review.replace(tzinfo=timezone.utc)
                        ).days
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
        """Delegate to DashboardService scan snapshot."""
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
        """Tổng hợp open positions và risk breaches."""
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

            # Risk breach: attempt to read stop_loss_breached flag if present
            risk_breach = 0
            try:
                from src.portfolio.models import Position as P  # type: ignore[import]

                risk_breach = (
                    await self.session.execute(
                        select(P)
                        .where(
                            P.user_id == self.user_id,
                            P.closed_at.is_(None),
                            P.stop_loss_breached.is_(True),
                        )
                    )
                ).scalars().all()
                risk_breach = len(risk_breach)
            except Exception:
                risk_breach = 0

            return PortfolioContext(
                total_positions=len(rows),
                risk_breach_count=risk_breach,
                top_exposed_tickers=list(rows[:5]),
            )
        except Exception:
            return PortfolioContext()
