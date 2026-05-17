"""Watchdog Service — daily orchestration of Invalidation Trigger Watchdog.

Owner: thesis segment.
Consumes WatchdogAgent (ai segment) via injection.
Consumes InvalidationService for auto-invalidation on URGENT_ALERT.
Consumes ThesisRepository for loading active theses.

Responsibility boundary:
  WatchdogService     → load active theses, build context, call agent, decide alert level,
                        persist ThesisHealthSnapshot, return WatchdogRunResult.
                        When alert_level=URGENT_ALERT and invalidation_svc is injected,
                        calls check_with_ai() and auto-invalidates if verdict=CONFIRMED.
  WatchdogAgent       → score health only, no DB writes
  InvalidationService → owns auto-invalidation rules + AI confirmation layer
  Bot/scheduler       → calls WatchdogService.run_for_user(), dispatches Discord from result

3-tier alert levels:
  OK             → no notification, health visible in morning brief only
  SILENT_WARNING → logged, surfaced in next morning brief (1 assumption threatened OR
                   stop-loss distance < 5%)
  URGENT_ALERT   → immediate Discord push (2+ assumptions threatened OR
                   stop-loss distance < 2% OR CRITICAL health)

Note: WatchdogService NEVER sends Discord messages directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import AssumptionStatus, Thesis, ThesisStatus
from src.thesis.repository import ThesisRepository

if TYPE_CHECKING:
    from src.ai.schemas.invalidation import InvalidationSignal
    from src.thesis.invalidation_service import InvalidationService

logger = get_logger(__name__)

# Thresholds
_STOP_LOSS_URGENT_PCT = 2.0    # < 2% from stop-loss → URGENT_ALERT
_STOP_LOSS_WARNING_PCT = 5.0   # < 5% from stop-loss → SILENT_WARNING
_STALE_REVIEW_DAYS = 14        # flag stale if no review in 14 days


@dataclass
class WatchdogTickerResult:
    """Result for a single thesis in a watchdog run."""

    thesis_id: int
    ticker: str
    alert_level: str          # OK | SILENT_WARNING | URGENT_ALERT
    health_score: int | None  # None if agent failed
    overall_health: str | None
    recommended_action: str | None
    discord_summary: str | None
    stop_loss_distance_pct: float | None = None
    agent_failed: bool = False
    invalidation_signal: InvalidationSignal | None = None
    auto_invalidated: bool = False


@dataclass
class WatchdogRunResult:
    """Aggregated result of a full watchdog run for one user."""

    user_id: str
    run_at: datetime
    results: list[WatchdogTickerResult] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def urgent_alerts(self) -> list[WatchdogTickerResult]:
        return [r for r in self.results if r.alert_level == "URGENT_ALERT"]

    @property
    def silent_warnings(self) -> list[WatchdogTickerResult]:
        return [r for r in self.results if r.alert_level == "SILENT_WARNING"]

    @property
    def healthy(self) -> list[WatchdogTickerResult]:
        return [r for r in self.results if r.alert_level == "OK"]

    def has_urgent(self) -> bool:
        return len(self.urgent_alerts) > 0


class WatchdogService:
    """Daily orchestration: load active theses → assess health → return alert results.

    Args:
        session:           AsyncSession per-request.
        watchdog_agent:    WatchdogAgent instance (ai segment). None → rule-based only.
        quote_service:     QuoteService for fetching current prices. None → skip price check.
        invalidation_svc:  InvalidationService with detector injected (optional).
                           When provided, URGENT_ALERT theses go through check_with_ai();
                           if verdict=CONFIRMED the thesis is auto-invalidated.
                           Pass None to skip AI invalidation layer (default, backward compat).
    """

    def __init__(
        self,
        session: AsyncSession,
        watchdog_agent: object | None = None,
        quote_service: object | None = None,
        invalidation_svc: InvalidationService | None = None,
    ) -> None:
        self._session = session
        self._repo = ThesisRepository(session)
        self._agent = watchdog_agent
        self._quote_service = quote_service
        self._invalidation_svc = invalidation_svc

    async def run_for_user(self, user_id: str) -> WatchdogRunResult:
        """Run watchdog for all active theses of a user."""
        run_result = WatchdogRunResult(user_id=user_id, run_at=datetime.now(UTC))

        try:
            theses = await self._repo.list_active(user_id)
        except Exception as exc:
            logger.error("watchdog.load_theses_failed", user_id=user_id, error=str(exc))
            return run_result

        for thesis in theses:
            try:
                ticker_result = await self._assess_thesis(thesis)
                run_result.results.append(ticker_result)
            except Exception as exc:
                logger.warning(
                    "watchdog.thesis_error",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    error=str(exc),
                )
                run_result.errors[thesis.ticker] = str(exc)

        logger.info(
            "watchdog.run_complete",
            user_id=user_id,
            total=len(theses),
            urgent=len(run_result.urgent_alerts),
            warnings=len(run_result.silent_warnings),
            healthy=len(run_result.healthy),
        )
        return run_result

    async def _assess_thesis(self, thesis: Thesis) -> WatchdogTickerResult:
        """Assess one thesis. Falls back to rule-based if agent unavailable."""
        from src.ai.prompts.watchdog import AssumptionSnapshot, WatchdogContext

        # Fetch current price if quote_service available
        current_price: float | None = None
        stop_loss_distance_pct: float | None = None
        if self._quote_service is not None:
            try:
                quote = await self._quote_service.get_quote(thesis.ticker)  # type: ignore
                current_price = quote.price
            except Exception as exc:
                logger.warning(
                    "watchdog.quote_fetch_failed",
                    ticker=thesis.ticker,
                    error=str(exc),
                )

        # Compute stop-loss distance
        if current_price and thesis.stop_loss and current_price > 0:
            stop_loss_distance_pct = (
                (current_price - thesis.stop_loss) / current_price * 100
            )

        # Fast rule-based override — skip AI if already CRITICAL by rules
        rule_alert = self._rule_based_check(
            thesis=thesis,
            stop_loss_distance_pct=stop_loss_distance_pct,
        )
        if rule_alert == "URGENT_ALERT" and self._agent is None:
            result = WatchdogTickerResult(
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                alert_level="URGENT_ALERT",
                health_score=None,
                overall_health="CRITICAL",
                recommended_action="REVIEW_URGENT",
                discord_summary=self._rule_based_summary(thesis, stop_loss_distance_pct),
                stop_loss_distance_pct=stop_loss_distance_pct,
                agent_failed=False,
            )
            await self._maybe_invalidate(
                result=result,
                thesis=thesis,
                current_price=current_price,
                watchdog_verdict="CRITICAL",
                watchdog_urgency="URGENT_ALERT",
            )
            return result

        # AI-based assessment
        if self._agent is not None:
            days_stale = self._days_since_last_review(thesis)
            ctx = WatchdogContext(
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                thesis_title=thesis.title,
                thesis_summary=thesis.summary or "",
                assumptions=[
                    AssumptionSnapshot(
                        assumption_id=a.id,
                        description=a.description,
                        current_status=a.status.value,
                        last_note=a.note or "",
                    )
                    for a in thesis.assumptions
                ],
                current_price=current_price,
                entry_price=thesis.entry_price,
                stop_loss=thesis.stop_loss,
                target_price=thesis.target_price,
                days_since_last_review=days_stale,
            )
            health = await self._agent.assess(ctx)  # type: ignore

            if health is not None:
                # Rule-based override: escalate if stop-loss very close
                alert_level = health.alert_level
                if (
                    stop_loss_distance_pct is not None
                    and stop_loss_distance_pct < _STOP_LOSS_URGENT_PCT
                    and alert_level != "URGENT_ALERT"
                ):
                    alert_level = "URGENT_ALERT"
                    logger.info(
                        "watchdog.stop_loss_escalation",
                        ticker=thesis.ticker,
                        stop_loss_distance_pct=stop_loss_distance_pct,
                    )

                result = WatchdogTickerResult(
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    alert_level=alert_level,
                    health_score=health.health_score,
                    overall_health=health.overall_health,
                    recommended_action=health.recommended_action,
                    discord_summary=health.discord_summary(thesis.ticker),
                    stop_loss_distance_pct=stop_loss_distance_pct,
                    agent_failed=False,
                )
                if alert_level == "URGENT_ALERT":
                    await self._maybe_invalidate(
                        result=result,
                        thesis=thesis,
                        current_price=current_price,
                        watchdog_verdict=health.overall_health,
                        watchdog_urgency=alert_level,
                    )
                return result

        # Fallback: agent unavailable or failed — use rule-based only
        result = WatchdogTickerResult(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            alert_level=rule_alert,
            health_score=None,
            overall_health=None,
            recommended_action=None,
            discord_summary=self._rule_based_summary(thesis, stop_loss_distance_pct),
            stop_loss_distance_pct=stop_loss_distance_pct,
            agent_failed=self._agent is not None,
        )
        if rule_alert == "URGENT_ALERT":
            await self._maybe_invalidate(
                result=result,
                thesis=thesis,
                current_price=current_price,
                watchdog_verdict=None,
                watchdog_urgency="URGENT_ALERT",
            )
        return result

    async def _maybe_invalidate(
        self,
        result: WatchdogTickerResult,
        thesis: Thesis,
        current_price: float | None,
        watchdog_verdict: str | None,
        watchdog_urgency: str | None,
    ) -> None:
        """Run check_with_ai() on URGENT_ALERT theses; auto-invalidate if CONFIRMED.

        Non-blocking: any failure is logged and swallowed — result is not affected.
        Mutates result.invalidation_signal and result.auto_invalidated in-place.
        """
        if self._invalidation_svc is None:
            return

        try:
            _rule_result, signal = await self._invalidation_svc.check_with_ai(
                thesis=thesis,
                current_score=float(result.health_score or 0),
                current_price=current_price,
                watchdog_verdict=watchdog_verdict,
                watchdog_urgency=watchdog_urgency,
            )
            result.invalidation_signal = signal

            if signal is not None and signal.verdict == "CONFIRMED":
                thesis.status = ThesisStatus.INVALIDATED
                thesis.closed_at = datetime.now(UTC)
                await self._repo.save(thesis)
                result.auto_invalidated = True
                logger.info(
                    "watchdog.auto_invalidated",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    action=signal.action,
                    confidence=signal.confidence,
                )

        except Exception as exc:
            logger.warning(
                "watchdog.invalidation_check_failed thesis_id=%s ticker=%s: %s",
                thesis.id,
                thesis.ticker,
                exc,
            )

    def _rule_based_check(
        self,
        thesis: Thesis,
        stop_loss_distance_pct: float | None,
    ) -> str:
        """Fast rule-based alert level without AI."""
        # Rule 1: stop-loss very close
        if stop_loss_distance_pct is not None:
            if stop_loss_distance_pct < _STOP_LOSS_URGENT_PCT:
                return "URGENT_ALERT"
            if stop_loss_distance_pct < _STOP_LOSS_WARNING_PCT:
                return "SILENT_WARNING"

        # Rule 2: too many invalid/uncertain assumptions
        invalid = sum(
            1 for a in thesis.assumptions
            if a.status in (AssumptionStatus.INVALID, AssumptionStatus.UNCERTAIN)
        )
        total = len(thesis.assumptions)
        if total > 0:
            ratio = invalid / total
            if ratio > 0.5:
                return "URGENT_ALERT"
            if ratio > 0.25:
                return "SILENT_WARNING"

        # Rule 3: stale thesis
        if self._days_since_last_review(thesis) > _STALE_REVIEW_DAYS:
            return "SILENT_WARNING"

        return "OK"

    def _rule_based_summary(self, thesis: Thesis, stop_dist: float | None) -> str:
        parts = [f"🔴 **{thesis.ticker}** — Rule-based watchdog alert"]
        if stop_dist is not None and stop_dist < _STOP_LOSS_WARNING_PCT:
            parts.append(f"⚠️ Cách stop-loss: {stop_dist:.1f}%")
        invalid = [a.description for a in thesis.assumptions
                   if a.status == AssumptionStatus.INVALID]
        if invalid:
            parts.append("🚫 Assumptions invalid: " + "; ".join(invalid[:3]))
        return "\n".join(parts)

    def _days_since_last_review(self, thesis: Thesis) -> int:
        if not thesis.reviews:
            from datetime import timezone
            delta = datetime.now(timezone.utc) - thesis.created_at.replace(
                tzinfo=UTC if thesis.created_at.tzinfo is None else thesis.created_at.tzinfo
            )
            return delta.days
        last = max(thesis.reviews, key=lambda r: r.reviewed_at)
        delta = datetime.now(UTC) - last.reviewed_at.replace(
            tzinfo=UTC if last.reviewed_at.tzinfo is None else last.reviewed_at.tzinfo
        )
        return delta.days
