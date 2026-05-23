"""IntelligenceEngine — orchestration core.

Owner: core segment.

Wave 1: signals-based synthesis (rule-based, no AI call).
Wave 2: replace _synthesize() with AIClient.generate_verdict(signals).
Wave 3: _dispatch() sends to briefing + bot.

Design principles:
- run_cycle() is the single entry point — snapshot → signals → synthesize → dispatch.
- Each step is replaceable without touching the others.
- All errors are caught per-step; partial output is always returned.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import EngineOutput, EngineVerdict, RankedSignal, SystemSnapshot, VerdictType
from src.core.signals import rank_signals
from src.core.snapshot import SystemSnapshotBuilder


class IntelligenceEngine:
    """Central AI orchestrator for one investor.

    Usage::

        engine = IntelligenceEngine(session, user_id)
        output = await engine.run_cycle()
    """

    DISPATCH_THRESHOLD = 0.65  # only dispatch if confidence >= this

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def run_cycle(self) -> EngineOutput:
        """Full cycle: build snapshot → rank signals → synthesize verdict → dispatch."""
        snapshot = await SystemSnapshotBuilder(self.session, self.user_id).build()
        signals = rank_signals(snapshot)
        verdict = await self._synthesize(snapshot, signals)
        dispatched = await self._dispatch(verdict)
        return EngineOutput(
            snapshot=snapshot,
            verdict=verdict,
            dispatched_to=dispatched,
        )

    # ------------------------------------------------------------------
    # Wave 1: signals-based rule synthesis
    # Wave 2: replace body with AIClient.generate_verdict(signals)
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        snap: SystemSnapshot,
        signals: list[RankedSignal],
    ) -> EngineVerdict:
        """Derive verdict from ranked signals using priority rules."""
        if not signals:
            return self._no_action_verdict(snap)

        top = signals[0]
        verdict_type, confidence = self._map_signal_to_verdict(top)

        risk_signals = [
            s.description for s in signals if s.source in ("portfolio", "watchlist")
        ][:5]
        next_watch = [
            s.description for s in signals if s.source in ("thesis", "market")
        ][:5]
        sources = list({s.source for s in signals})

        summary = " | ".join(
            f"{s.source}:{s.urgency_score:.2f}" for s in signals[:4]
        )

        return EngineVerdict(
            verdict_id=str(uuid.uuid4()),
            verdict=verdict_type,
            confidence=confidence,
            risk_signals=risk_signals,
            next_watch_items=next_watch,
            action=self._derive_action(verdict_type, snap),
            reasoning_summary=summary,
            sources=sources,
            generated_at=datetime.now(timezone.utc),
        )

    def _map_signal_to_verdict(
        self, top: RankedSignal
    ) -> tuple[VerdictType, float]:
        """Map the highest-scored signal to a verdict + confidence."""
        if top.source == "portfolio":
            return "RISK_ALERT", min(0.95, 0.70 + top.urgency_score * 0.25)
        if top.source == "thesis" and "invalidate" in top.description.lower():
            return "REVIEW_THESIS", min(0.90, 0.65 + top.urgency_score * 0.25)
        if top.source == "watchlist":
            return "RISK_ALERT", min(0.85, 0.60 + top.urgency_score * 0.25)
        if top.source == "thesis":
            return "REVIEW_THESIS", min(0.80, 0.55 + top.urgency_score * 0.25)
        if top.source == "market" and top.urgency_score >= 0.4:
            return "HOLD", min(0.75, 0.50 + top.urgency_score * 0.25)
        return "NO_ACTION", 0.40

    def _no_action_verdict(self, snap: SystemSnapshot) -> EngineVerdict:
        return EngineVerdict(
            verdict_id=str(uuid.uuid4()),
            verdict="NO_ACTION",
            confidence=0.40,
            risk_signals=[],
            next_watch_items=[],
            action="Không có action ưu tiên. Hệ thống ổn định.",
            reasoning_summary="0 signals detected across all segments",
            sources=[],
            generated_at=datetime.now(timezone.utc),
        )

    def _derive_action(self, verdict: VerdictType, snap: SystemSnapshot) -> str:
        if verdict == "RISK_ALERT":
            tickers = ", ".join(
                a.ticker for a in snap.watchlist_alerts[:3]
            ) or ", ".join(snap.portfolio.top_exposed_tickers[:3])
            return f"Kiểm tra ngay: {tickers}" if tickers else "Kiểm tra risk breach"
        if verdict == "REVIEW_THESIS":
            tickers = ", ".join(t.ticker for t in snap.thesis_due_review[:3])
            return f"Review thesis: {tickers}" if tickers else "Review thesis overdue"
        if verdict == "HOLD":
            tickers = ", ".join(s.ticker for s in snap.market_anomalies[:3])
            return f"Theo dõi tín hiệu thị trường: {tickers}" if tickers else "Theo dõi thị trường"
        return "Không có action ưu tiên. Hệ thống ổn định."

    # ------------------------------------------------------------------
    # Wave 1: dispatch chỉ log
    # Wave 3: gọi briefing.push() + bot.notify()
    # ------------------------------------------------------------------

    async def _dispatch(self, verdict: EngineVerdict) -> list[str]:
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
