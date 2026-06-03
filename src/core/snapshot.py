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

        snapshot = await SystemSnapshotBuilder(
            session, user_id,
            trigger_source="scheduler",
            signal_engine_summary="...",
        ).build()
    """

    OVERDUE_DAYS = 14  # thesis older than N days without AI review = overdue
    STALE_DAYS = 3    # thesis without any review in N days = stale

    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        trigger_source: str = "",
        signal_engine_summary: str | None = None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.trigger_source = trigger_source
        self.signal_engine_summary = signal_engine_summary

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
            market_phase=self.trigger_source or "unknown",
        )

        now = datetime.now(timezone.utc)
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
            # timestamps
            captured_at=now,
            timestamp=now,
            # forwarded caller context
            trigger_source=self.trigger_source,
            signal_engine_summary=self.signal_engine_summary,
        )

    # ------------------------------------------------------------------
    # Private fetchers — each returns a safe default on any exception
    # ------------------------------------------------------------------

    async def _fetch_alerts(self) -> list[WatchlistAlert]:
        """Alerts đã trigger, chưa được dismiss, và ticker chưa bị snooze."""
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

            # Filter out tickers currently in snooze window.
            # Wrapped separately so a WatchlistItem import/query failure
            # degrades gracefully — alerts are returned unfiltered.
            snoozed_tickers: set[str] = set()
            try:
                from src.watchlist.models import WatchlistItem  # type: ignore[import]

                now = datetime.now(timezone.utc)
                snoozed_rows = (
                    await self.session.execute(
                        select(WatchlistItem.ticker).where(
                            WatchlistItem.user_id == self.user_id,
                            WatchlistItem.snoozed_until.isnot(None),
                            WatchlistItem.snoozed_until > now,
                        )
                    )
                ).scalars().all()
                snoozed_tickers = {t.upper() for t in snoozed_rows}
            except Exception:
                pass  # snooze filter unavailable — return all alerts

            return [
                WatchlistAlert(
                    ticker=r.ticker,
                    alert_type=r.alert_type,
                    triggered_at=r.triggered_at,
                    note=getattr(r, "note", None),
                )
                for r in rows
                if r.ticker.upper() not in snoozed_tickers
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
        """Build PortfolioContext via the portfolio segment public interface.

        Uses get_portfolio_context() — the single approved entry point for
        cross-segment portfolio reads. Raw ORM imports from portfolio.models
        are forbidden here (boundary rule).

        risk_breach_count is fetched separately because stop_loss_breached
        is not surfaced by get_portfolio_context().
        """
        try:
            from src.portfolio import get_portfolio_context  # type: ignore[import]

            pf = await get_portfolio_context(
                self.session,
                self.user_id,
                include_prices=False,  # hot path — skip live price lookup
            )

            # Aggregate unrealized PnL % from cost basis when prices unavailable
            # (total_unrealized_pnl is None when include_prices=False)
            unrealized_pnl_pct: float | None = None
            if pf.total_unrealized_pnl is not None and pf.total_cost_basis > 0:
                unrealized_pnl_pct = round(
                    pf.total_unrealized_pnl / pf.total_cost_basis * 100, 2
                )

            # risk_breach_count: separate query — stop_loss_breached not in public interface
            risk_breach = 0
            try:
                from src.portfolio.models import Position  # type: ignore[import]

                breached = (
                    await self.session.execute(
                        select(Position)
                        .where(
                            Position.user_id == self.user_id,
                            Position.closed_at.is_(None),
                            Position.stop_loss_breached.is_(True),
                        )
                    )
                ).scalars().all()
                risk_breach = len(breached)
            except Exception:
                risk_breach = 0

            return PortfolioContext(
                total_positions=pf.position_count,
                risk_breach_count=risk_breach,
                total_market_value=pf.total_market_value,
                top_exposed_tickers=pf.tickers[:5],
                unrealized_pnl_pct=unrealized_pnl_pct,
            )

        except Exception:
            return PortfolioContext()
