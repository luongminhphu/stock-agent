"""IntelligenceEngine — orchestration core.

Owner: core segment.

Wave 1: rule-based synthesis (urgency ranking, no AI call).
Wave 2: replace _synthesize() with AIClient.generate_verdict(signals).
Wave 3: _dispatch() sends to briefing + bot.

Design principles:
- run_cycle() is the single entry point — snapshot → synthesize → dispatch.
- Each step is replaceable without touching the others.
- All errors are caught per-step; partial output is always returned.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import EngineOutput, EngineVerdict, SystemSnapshot, VerdictType
from src.core.snapshot import SystemSnapshotBuilder


class IntelligenceEngine:
    """Central AI orchestrator for one investor.

    Usage::

        engine = IntelligenceEngine(session, user_id)
        output = await engine.run_cycle()
    """

    # Confidence thresholds
    DISPATCH_THRESHOLD = 0.65  # only dispatch if confidence >= this

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def run_cycle(self) -> EngineOutput:
        """Full cycle: build snapshot → synthesize verdict → dispatch."""
        snapshot = await SystemSnapshotBuilder(self.session, self.user_id).build()
        verdict = await self._synthesize(snapshot)
        dispatched = await self._dispatch(verdict, snapshot)
        return EngineOutput(
            snapshot=snapshot,
            verdict=verdict,
            dispatched_to=dispatched,
        )

    # ------------------------------------------------------------------
    # Wave 1: rule-based synthesis
    # Wave 2: replace with AIClient call
    # ------------------------------------------------------------------

    async def _synthesize(self, snap: SystemSnapshot) -> EngineVerdict:
        """Derive verdict from snapshot signals using urgency rules."""
        risk_signals: list[str] = []
        next_watch: list[str] = []
        verdict_type: VerdictType = "NO_ACTION"
        confidence = 0.4

        # Priority 1 — triggered alerts (critical urgency)
        if snap.watchlist_alerts:
            verdict_type = "RISK_ALERT"
            confidence = 0.85
            risk_signals = [
                f"{a.ticker}: {a.alert_type}" for a in snap.watchlist_alerts[:5]
            ]

        # Priority 2 — overdue thesis reviews
        if snap.thesis_due_review:
            if verdict_type == "NO_ACTION":
                verdict_type = "REVIEW_THESIS"
                confidence = 0.75
            next_watch = [
                f"{t.ticker} (overdue {t.days_overdue}d)"
                for t in snap.thesis_due_review[:5]
            ]

        # Priority 3 — market scan anomalies
        if snap.market_anomalies and verdict_type == "NO_ACTION":
            verdict_type = "HOLD"
            confidence = 0.60
            next_watch += [
                f"{s.ticker}: {s.signal_type}" for s in snap.market_anomalies[:3]
            ]

        sources: list[str] = []
        if snap.watchlist_alerts:                    sources.append("watchlist")
        if snap.thesis_due_review:                   sources.append("thesis")
        if snap.market_anomalies:                    sources.append("market_scan")
        if snap.portfolio_context.total_positions:   sources.append("portfolio")

        summary_parts = [
            f"{len(snap.watchlist_alerts)} triggered alert(s)",
            f"{len(snap.thesis_due_review)} overdue review(s)",
            f"{len(snap.market_anomalies)} market signal(s)",
            f"{snap.portfolio_context.total_positions} open position(s)",
        ]

        return EngineVerdict(
            verdict_id=str(uuid.uuid4()),
            verdict=verdict_type,
            confidence=confidence,
            risk_signals=risk_signals,
            next_watch_items=next_watch,
            action=self._derive_action(verdict_type, snap),
            reasoning_summary=" | ".join(summary_parts),
            sources=sources,
            generated_at=datetime.now(timezone.utc),
        )

    def _derive_action(self, verdict: VerdictType, snap: SystemSnapshot) -> str:
        if verdict == "RISK_ALERT" and snap.watchlist_alerts:
            tickers = ", ".join(a.ticker for a in snap.watchlist_alerts[:3])
            return f"Kiểm tra ngay alerts: {tickers}"
        if verdict == "REVIEW_THESIS" and snap.thesis_due_review:
            tickers = ", ".join(t.ticker for t in snap.thesis_due_review[:3])
            return f"Review thesis: {tickers}"
        if verdict == "HOLD" and snap.market_anomalies:
            tickers = ", ".join(s.ticker for s in snap.market_anomalies[:3])
            return f"Theo dõi tín hiệu thị trường: {tickers}"
        return "Không có action ưu tiên. Hệ thống ổn định."

    # ------------------------------------------------------------------
    # Wave 1: dispatch chỉ log
    # Wave 3: gọi briefing.push() + bot.notify()
    # ------------------------------------------------------------------

    async def _dispatch(self, verdict: EngineVerdict, snap: SystemSnapshot) -> list[str]:
        """Route verdict to downstream segments.

        Wave 1: log only.
        Wave 3: dispatch to briefing + bot when confidence >= DISPATCH_THRESHOLD.
        """
        dispatched: list[str] = []
        if verdict.confidence >= self.DISPATCH_THRESHOLD:
            # TODO Wave 3: await briefing_service.push(verdict)
            # TODO Wave 3: await bot_notifier.notify(verdict)
            dispatched.append("log")
        return dispatched
