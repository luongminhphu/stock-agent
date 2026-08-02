"""Stop-breach auto-invalidation scan — Wave 6c.

Owner: thesis segment. Rules live here; bot/scheduler chỉ gọi scan() và
gửi notification — không chứa logic invalidate.

Vấn đề giải quyết: giá đã xuyên stop-loss nhưng không luồng nào kiểm tra
điều đó một cách chủ động. Case thực tế: HPG thesis BULLISH stop 23,000,
giá 21,700, score vẫn 95.3 "Strong" — investor nhìn dashboard tưởng thesis
khỏe trong khi nó đã chết theo chính điều kiện của nó.

Flow per thesis (ACTIVE, có stop_loss):
  1. Cooldown check — không re-scan trong auto_invalidate_cooldown_hours
     kể từ lần scan breach gần nhất (tracked trong memory qua detector log,
     fallback: dùng updated_at nếu chưa từng scan).
  2. Rule check: InvalidationService.check_with_price() — pure, fast, no AI.
  3. Breached + auto_invalidate_enabled → AI confirm:
     ThesisInvalidationDetector.detect() (non-blocking; AI fail → skip).
  4. CONFIRMED + confidence >= auto_invalidate_min_confidence →
     thesis.status = INVALIDATED + emit ThesisClosedEvent
     (post-mortem + memory chains fire như invalidate thủ công).
  5. Mọi kết quả (kể cả skip) trả về StopBreachOutcome để caller notify.

Kill switch: settings.auto_invalidate_enabled = False → rule check vẫn
chạy và kết quả được log (OBSERVED), nhưng không AI confirm, không mutate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.invalidation_service import InvalidationService
from src.thesis.models import Thesis, ThesisStatus

if TYPE_CHECKING:
    from src.ai.agents.invalidation_detector import ThesisInvalidationDetector

logger = get_logger(__name__)


@dataclass
class StopBreachOutcome:
    """Kết quả scan cho 1 thesis breached — để caller (scheduler) notify."""

    thesis_id: int
    ticker: str
    current_price: float
    stop_loss: float
    overshoot_pct: float
    action: str                    # "invalidated" | "observed" | "ai_not_confirmed" | "ai_failed"
    ai_verdict: str | None = None  # CONFIRMED / SUSPECTED / CLEARED / None
    ai_confidence: float | None = None
    ai_action: str | None = None   # exit_signal / review / reduce / hold
    reason: str = ""


@dataclass
class StopBreachScanResult:
    outcomes: list[StopBreachOutcome] = field(default_factory=list)

    @property
    def invalidated(self) -> list[StopBreachOutcome]:
        return [o for o in self.outcomes if o.action == "invalidated"]

    @property
    def observed(self) -> list[StopBreachOutcome]:
        return [o for o in self.outcomes if o.action != "invalidated"]


class StopBreachService:
    """Scan ACTIVE theses for stop-loss breach; auto-invalidate khi AI xác nhận.

    Args:
        session:    Caller-owned session (commits happen via repo.save).
        quote_service: QuoteService từ bootstrap (tránh circular import).
        detector:   ThesisInvalidationDetector — None = rule-only mode
                    (breach vẫn report nhưng không bao giờ invalidate).
        cooldown_hours: Min hours giữa 2 lần scan breach cùng 1 thesis.
        min_confidence: CONFIRMED + confidence >= ngưỡng mới invalidate.
        enabled:    False = observe-only (log + report, không mutate).
    """

    def __init__(
        self,
        session: AsyncSession,
        quote_service: object,
        detector: ThesisInvalidationDetector | None = None,
        cooldown_hours: float = 24.0,
        min_confidence: float = 0.7,
        enabled: bool = True,
    ) -> None:
        self._session = session
        self._quote_service = quote_service
        self._invalidation_svc = InvalidationService(detector=detector)
        self._cooldown = timedelta(hours=cooldown_hours)
        self._min_confidence = min_confidence
        self._enabled = enabled

    async def scan(self, user_id: str) -> StopBreachScanResult:
        theses = await self._load_candidates(user_id)
        if not theses:
            return StopBreachScanResult()

        tickers = sorted({t.ticker for t in theses})
        price_map: dict[str, float] = {}
        for ticker in tickers:
            try:
                quote = await self._quote_service.get_quote(ticker)  # type: ignore[attr-defined]
                price_map[ticker] = quote.price
            except Exception as exc:
                logger.warning(
                    "stop_breach.quote_fetch_failed", ticker=ticker, error=str(exc)
                )

        result = StopBreachScanResult()
        for thesis in theses:
            price = price_map.get(thesis.ticker)
            if price is None:
                continue
            outcome = await self._process(thesis, price)
            if outcome is not None:
                result.outcomes.append(outcome)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_candidates(self, user_id: str) -> list[Thesis]:
        # selectinload: InvalidationService đọc thesis.assumptions trong rule
        # check — lazy-load trong async context sẽ raise MissingGreenlet.
        from sqlalchemy.orm import selectinload

        rows = await self._session.execute(
            select(Thesis)
            .options(selectinload(Thesis.assumptions))
            .where(
                Thesis.user_id == user_id,
                Thesis.status == ThesisStatus.ACTIVE,
                Thesis.stop_loss.isnot(None),
            )
        )
        return list(rows.scalars().all())

    def _in_cooldown(self, thesis: Thesis) -> bool:
        # Không re-scan thesis vừa được đụng vào (review/update gần đây) —
        # updated_at là proxy bảo thủ: thesis đang được chăm sóc thì để
        # các luồng khác (drift review, watchdog) xử lý trước.
        if thesis.updated_at is None:
            return False
        updated = thesis.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return (datetime.now(UTC) - updated) < self._cooldown

    async def _process(self, thesis: Thesis, price: float) -> StopBreachOutcome | None:
        rule = self._invalidation_svc.check_with_price(
            thesis, current_score=float(thesis.score or 0), current_price=price
        )
        if not rule.stop_loss_breached:
            return None

        overshoot = abs(price - thesis.stop_loss) / thesis.stop_loss * 100  # type: ignore[operator]

        if self._in_cooldown(thesis):
            logger.debug(
                "stop_breach.cooldown_skip", thesis_id=thesis.id, ticker=thesis.ticker
            )
            return None

        base = dict(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            current_price=price,
            stop_loss=thesis.stop_loss,
            overshoot_pct=round(overshoot, 2),
            reason=rule.reason,
        )

        logger.info(
            "stop_breach.detected",
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            price=price,
            stop_loss=thesis.stop_loss,
            overshoot_pct=round(overshoot, 2),
        )

        if not self._enabled:
            return StopBreachOutcome(action="observed", **base)

        # AI confirmation layer — non-blocking; session+user_id để detector
        # log episodic memory (boundary: caller owns session, detector chỉ ghi log).
        _rule, signal = await self._invalidation_svc.check_with_ai(
            thesis=thesis,
            current_score=float(thesis.score or 0),
            current_price=price,
            session=self._session,
            user_id=thesis.user_id,
        )

        if signal is None:
            # Detector không được inject hoặc AI fail → không tự ý invalidate
            return StopBreachOutcome(action="ai_failed", **base)

        if signal.verdict != "CONFIRMED" or (signal.confidence or 0) < self._min_confidence:
            return StopBreachOutcome(
                action="ai_not_confirmed",
                ai_verdict=signal.verdict,
                ai_confidence=signal.confidence,
                ai_action=signal.action,
                **base,
            )

        # CONFIRMED + đủ confidence → invalidate qua cùng path với thủ công
        thesis.status = ThesisStatus.INVALIDATED
        thesis.closed_at = datetime.now(UTC)
        await self._session.flush()
        logger.info(
            "stop_breach.auto_invalidated",
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            confidence=signal.confidence,
            action=signal.action,
        )
        await self._emit_closed(thesis)

        return StopBreachOutcome(
            action="invalidated",
            ai_verdict=signal.verdict,
            ai_confidence=signal.confidence,
            ai_action=signal.action,
            **base,
        )

    async def _emit_closed(self, thesis: Thesis) -> None:
        """Emit ThesisClosedEvent — fire post-mortem + memory chains.

        Same payload shape as ThesisService.invalidate() so downstream
        subscribers cannot distinguish auto vs manual (đúng ý đồ).
        """
        try:
            from src.platform.event_bus import get_event_bus
            from src.platform.events import ThesisClosedEvent

            await get_event_bus().publish(
                ThesisClosedEvent(
                    thesis_id=thesis.id,
                    user_id=thesis.user_id or "",
                    ticker=thesis.ticker or "",
                    close_reason="stop_breach_auto_invalidated",
                    thesis_title=thesis.title or "",
                    thesis_summary=thesis.summary or "",
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "stop_breach.closed_event_failed", thesis_id=thesis.id, error=str(exc)
            )
