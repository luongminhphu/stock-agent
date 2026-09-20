"""Watchdog Service — orchestration của Invalidation Trigger Watchdog (đánh giá theo assumption).

Owner: thesis segment.
Consumes WatchdogAgent (ai segment) qua injection.
Consumes InvalidationService để auto-invalidate khi URGENT_ALERT và AI xác nhận.
Consumes ThesisRepository để load thesis ACTIVE (kèm assumptions + reviews).
Consumes market qua ``load_price_snapshots`` (TickerContext theo lô, fallback get_quote).

Responsibility boundary:
  WatchdogService     → load thesis, lấy giá theo lô, dựng WatchdogContext, gọi agent,
                        quyết định alert level, trả WatchdogRunResult.
                        Khi alert_level=URGENT_ALERT và invalidation_svc được inject:
                        gọi check_with_ai(); verdict=CONFIRMED → auto-invalidate.
  WatchdogAgent       → chấm health, không ghi DB
  InvalidationService → sở hữu rule auto-invalidation + lớp AI confirmation
  StopBreachService   → sở hữu tín hiệu XUYÊN stop (đã chạy mỗi 15 phút). Watchdog không
                        alert lại khi giá đã xuyên stop; chỉ dùng near_stop làm tín hiệu sớm.
  Bot/scheduler       → gọi run_for_user(), render Discord từ WatchdogRunResult

3-tier alert levels:
  OK             → không notify
  SILENT_WARNING → gộp vào embed sáng (1 assumption bị đe dọa, gần stop < 1 ATR14
                   hoặc pct fallback < 5%, hoặc thesis chưa review > 14 ngày)
  URGENT_ALERT   → push Discord ngay (>50% assumptions invalid/uncertain, health CRITICAL,
                   hoặc pct fallback < 2% khi không có ATR)

Khoảng cách stop (Wave D2): ưu tiên ``PriceSnapshot.stop_distance_atr`` + ``NEAR_STOP_ATR``
(cùng ngưỡng với StopBreachService và readmodel). Ngưỡng % chỉ là fallback khi thiếu ATR14
(data fallback / quote-only).

Không persist gì ngoài auto-invalidate. WatchdogService KHÔNG gửi Discord.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import AssumptionStatus, Thesis, ThesisStatus
from src.thesis.price_snapshot import PriceSnapshot, load_price_snapshots
from src.thesis.repository import ThesisRepository
from src.thesis.stop_breach_service import NEAR_STOP_ATR

if TYPE_CHECKING:
    from src.ai.schemas.invalidation import InvalidationSignal
    from src.thesis.invalidation_service import InvalidationService

logger = get_logger(__name__)

# Fallback theo % khi không có ATR14 (giữ hành vi cũ cho data quote-only)
_STOP_LOSS_URGENT_PCT = 2.0  # < 2% từ stop-loss → URGENT_ALERT
_STOP_LOSS_WARNING_PCT = 5.0  # < 5% từ stop-loss → SILENT_WARNING
_STALE_REVIEW_DAYS = 14  # chưa review 14 ngày → stale

_LEVEL_ORDER = {"OK": 0, "SILENT_WARNING": 1, "URGENT_ALERT": 2}


def _max_level(*levels: str | None) -> str:
    present = [lv for lv in levels if lv]
    if not present:
        return "OK"
    return max(present, key=lambda lv: _LEVEL_ORDER.get(lv, 0))


@dataclass
class WatchdogTickerResult:
    """Kết quả cho một thesis trong một lần chạy watchdog."""

    thesis_id: int
    ticker: str
    alert_level: str  # OK | SILENT_WARNING | URGENT_ALERT
    health_score: int | None  # None nếu agent fail / không có agent
    overall_health: str | None
    recommended_action: str | None
    discord_summary: str | None
    stop_loss_distance_pct: float | None = None
    stop_distance_atr: float | None = None  # Wave D2: (price - stop) / ATR14
    source_quality: str | None = None  # live | stale | fallback | quote | None
    agent_failed: bool = False
    invalidation_signal: InvalidationSignal | None = None
    auto_invalidated: bool = False

    @property
    def near_stop(self) -> bool:
        return self.stop_distance_atr is not None and 0 < self.stop_distance_atr < NEAR_STOP_ATR


@dataclass
class WatchdogRunResult:
    """Kết quả gộp của một lần chạy watchdog cho một user."""

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

    def has_notable(self) -> bool:
        return bool(self.urgent_alerts or self.silent_warnings)


class WatchdogService:
    """Orchestration: load thesis ACTIVE → lấy giá theo lô → chấm health → alert level.

    Args:
        session:                AsyncSession per-run.
        watchdog_agent:         WatchdogAgent (ai). None → chỉ rule-based.
        quote_service:          QuoteService — fallback từng mã khi thiếu TickerContext.
        invalidation_svc:       InvalidationService có detector (tùy chọn). Khi có,
                                URGENT_ALERT đi qua check_with_ai(); CONFIRMED → auto-invalidate.
        ticker_context_service: market.TickerContextService — bulk giá + ATR14 +
                                dòng bối cảnh kỹ thuật cho prompt (Wave D2).
        min_confidence:         CONFIRMED + confidence >= ngưỡng mới auto-invalidate
                                (cùng semantics settings.auto_invalidate_min_confidence
                                của StopBreachService).
    """

    def __init__(
        self,
        session: AsyncSession,
        watchdog_agent: Any | None = None,
        quote_service: Any | None = None,
        invalidation_svc: InvalidationService | None = None,
        ticker_context_service: Any | None = None,
        min_confidence: float = 0.7,
    ) -> None:
        self._session = session
        self._repo = ThesisRepository(session)
        self._agent = watchdog_agent
        self._quote_service = quote_service
        self._invalidation_svc = invalidation_svc
        self._ticker_context_service = ticker_context_service
        self._min_confidence = min_confidence

    async def run_for_user(self, user_id: str) -> WatchdogRunResult:
        """Chạy watchdog cho toàn bộ thesis ACTIVE của user."""
        run_result = WatchdogRunResult(user_id=user_id, run_at=datetime.now(UTC))

        try:
            theses = await self._repo.list_active_for_user(user_id)
        except Exception as exc:
            logger.error("watchdog.load_theses_failed", user_id=user_id, error=str(exc))
            return run_result

        if not theses:
            return run_result

        snapshots = await load_price_snapshots(
            [t.ticker for t in theses],
            ticker_context_service=self._ticker_context_service,
            quote_service=self._quote_service,
            log_event="watchdog.price_snapshot",
        )

        for thesis in theses:
            try:
                snap = snapshots.get((thesis.ticker or "").upper())
                ticker_result = await self._assess_thesis(thesis, snap)
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
            errors=len(run_result.errors),
        )
        return run_result

    async def _assess_thesis(
        self, thesis: Thesis, snap: PriceSnapshot | None
    ) -> WatchdogTickerResult:
        """Chấm một thesis. Không có agent / agent fail → rule-based."""
        from src.ai.prompts.watchdog import AssumptionSnapshot, WatchdogContext

        current_price = snap.price if snap else None
        stop_distance_atr = snap.stop_distance_atr(thesis.stop_loss) if snap else None
        stop_loss_distance_pct: float | None = None
        if current_price and thesis.stop_loss and current_price > 0:
            stop_loss_distance_pct = (current_price - thesis.stop_loss) / current_price * 100

        stop_level = self._stop_alert_level(stop_loss_distance_pct, stop_distance_atr)
        rule_alert = self._rule_based_check(thesis, stop_level)

        def _result(**kw: Any) -> WatchdogTickerResult:
            return WatchdogTickerResult(
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                stop_loss_distance_pct=stop_loss_distance_pct,
                stop_distance_atr=stop_distance_atr,
                source_quality=snap.source_quality if snap else None,
                **kw,
            )

        # Rule đã URGENT và không có agent → không tốn token
        if rule_alert == "URGENT_ALERT" and self._agent is None:
            result = _result(
                alert_level="URGENT_ALERT",
                health_score=None,
                overall_health="CRITICAL",
                recommended_action="REVIEW_URGENT",
                discord_summary=self._rule_based_summary(thesis, stop_loss_distance_pct),
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
                ticker_context=snap.prompt_context if snap else "",
            )
            health = await self._agent.assess(
                ctx, session=self._session, user_id=thesis.user_id, trigger="watchdog"
            )

            if health is not None:
                # Rule stop luôn được phép nâng mức (không hạ) so với verdict AI
                alert_level = _max_level(health.alert_level, stop_level)
                if alert_level != health.alert_level:
                    logger.info(
                        "watchdog.stop_escalation",
                        ticker=thesis.ticker,
                        ai_level=health.alert_level,
                        stop_level=stop_level,
                        stop_distance_atr=stop_distance_atr,
                        stop_loss_distance_pct=stop_loss_distance_pct,
                    )

                result = _result(
                    alert_level=alert_level,
                    health_score=health.health_score,
                    overall_health=health.overall_health,
                    recommended_action=health.recommended_action,
                    discord_summary=health.discord_summary(thesis.ticker),
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

        # Fallback: không có agent hoặc agent fail → rule-based
        result = _result(
            alert_level=rule_alert,
            health_score=None,
            overall_health=None,
            recommended_action=None,
            discord_summary=self._rule_based_summary(thesis, stop_loss_distance_pct),
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
        """check_with_ai() cho thesis URGENT_ALERT; CONFIRMED → auto-invalidate.

        Non-blocking: lỗi chỉ log, không ảnh hưởng result.
        Mutate result.invalidation_signal và result.auto_invalidated in-place.
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

            if signal is None:
                return
            if signal.verdict != "CONFIRMED" or (signal.confidence or 0) < self._min_confidence:
                logger.info(
                    "watchdog.invalidation_not_confirmed",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    verdict=signal.verdict,
                    confidence=signal.confidence,
                    min_confidence=self._min_confidence,
                )
                return
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
            # Emit ThesisClosedEvent để post-mortem + memory chain chạy
            try:
                from src.platform.event_bus import get_event_bus
                from src.platform.events import ThesisClosedEvent

                await get_event_bus().publish(
                    ThesisClosedEvent(
                        thesis_id=thesis.id,
                        user_id=thesis.user_id or "",
                        ticker=thesis.ticker or "",
                        close_reason="watchdog_auto_invalidated",
                        thesis_title=thesis.title or "",
                        thesis_summary=thesis.summary or "",
                    )
                )
            except Exception as _ev_exc:  # noqa: BLE001
                logger.warning(
                    "watchdog.thesis_closed_event.emit_failed",
                    thesis_id=thesis.id,
                    error=str(_ev_exc),
                )

        except Exception as exc:
            logger.warning(
                "watchdog.invalidation_check_failed",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                error=str(exc),
            )

    @staticmethod
    def _stop_alert_level(
        stop_loss_distance_pct: float | None,
        stop_distance_atr: float | None,
    ) -> str | None:
        """Mức alert riêng cho khoảng cách stop. None = không có dữ liệu.

        - Có ATR14: đã xuyên (<= 0) → "OK" (StopBreachService sở hữu tín hiệu này,
          không alert đôi); near_stop (< NEAR_STOP_ATR) → SILENT_WARNING.
        - Không có ATR14: fallback % (< 2% URGENT, < 5% WARNING).
        """
        if stop_distance_atr is not None:
            if stop_distance_atr <= 0:
                return "OK"
            if stop_distance_atr < NEAR_STOP_ATR:
                return "SILENT_WARNING"
            return "OK"
        if stop_loss_distance_pct is not None:
            if stop_loss_distance_pct < _STOP_LOSS_URGENT_PCT:
                return "URGENT_ALERT"
            if stop_loss_distance_pct < _STOP_LOSS_WARNING_PCT:
                return "SILENT_WARNING"
            return "OK"
        return None

    def _rule_based_check(self, thesis: Thesis, stop_level: str | None) -> str:
        """Alert level rule-based (không AI) = max(stop, assumptions, stale)."""
        assumption_level = "OK"
        invalid = sum(
            1
            for a in thesis.assumptions
            if a.status in (AssumptionStatus.INVALID, AssumptionStatus.UNCERTAIN)
        )
        total = len(thesis.assumptions)
        if total > 0:
            ratio = invalid / total
            if ratio > 0.5:
                assumption_level = "URGENT_ALERT"
            elif ratio > 0.25:
                assumption_level = "SILENT_WARNING"

        stale_level = (
            "SILENT_WARNING" if self._days_since_last_review(thesis) > _STALE_REVIEW_DAYS else "OK"
        )
        return _max_level(stop_level, assumption_level, stale_level)

    def _rule_based_summary(self, thesis: Thesis, stop_dist: float | None) -> str:
        parts = [f"🔴 **{thesis.ticker}** — Rule-based watchdog alert"]
        if stop_dist is not None and stop_dist < _STOP_LOSS_WARNING_PCT:
            parts.append(f"⚠️ Cách stop-loss: {stop_dist:.1f}%")
        invalid = [
            a.description for a in thesis.assumptions if a.status == AssumptionStatus.INVALID
        ]
        if invalid:
            parts.append("🚫 Assumptions invalid: " + "; ".join(invalid[:3]))
        return "\n".join(parts)

    def _days_since_last_review(self, thesis: Thesis) -> int:
        reviews = getattr(thesis, "reviews", None) or []
        if not reviews:
            created = thesis.created_at
            if created is None:
                return 0
            delta = datetime.now(UTC) - created.replace(
                tzinfo=UTC if created.tzinfo is None else created.tzinfo
            )
            return delta.days
        last = max(reviews, key=lambda r: r.reviewed_at)
        delta = datetime.now(UTC) - last.reviewed_at.replace(
            tzinfo=UTC if last.reviewed_at.tzinfo is None else last.reviewed_at.tzinfo
        )
        return delta.days
