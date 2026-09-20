"""
ThesisHealthSnapshot — thesis segment read model for AI context injection.

Owner: thesis segment.
Consumers: ai.context_builder (read-only). Never import from bot/api/briefing directly.

Responsibilities:
- Compute a typed, normalised health snapshot per active thesis.
- Expose format_for_prompt() so ContextBuilder can inject a structured,
  information-dense thesis block into every AI agent call.

Non-responsibilities:
- Does NOT write to DB.
- Does NOT call AI.
- Does NOT mutate thesis state — pure read path.

Design:
  build_thesis_health_snapshots(session, user_id, ticker_context_service=, quote_service=)
    └─ ThesisService.list_active()          → list of ORM thesis objects (reviews eager)
    └─ load_price_snapshots(tickers)        → giá + ATR14 theo lô (Wave D4; None → không giá)
    └─ ScoringService.compute(thesis)       → float 0.0–100.0, normalized to 0.0–1.0
    └─ _compute_snapshot(thesis, score, price_snapshot) → ThesisHealthSnapshot
    └─ sort by urgency DESC, cap at MAX_THESES

urgency_flag priority order (highest → lowest):
  INVALIDATED  → thesis.status == "invalidated" (should not appear in active list
                 but guard anyway)
  AT_RISK      → stop_proximity in (BREACHED, CRITICAL, NEAR) — rule chung
                 thesis.price_snapshot (ATR14, fallback %) — OR health_score <= AT_RISK_SCORE_THRESHOLD
  REVIEW_DUE   → days_since_review >= REVIEW_DUE_DAYS
  OK           → everything else

Wave D4: trước đây ``current_price`` đọc từ attribute không tồn tại trên ORM →
distance_to_stop luôn None, AT_RISK theo stop không bao giờ bật; ``last_verdict``
đọc ``thesis.last_verdict`` (không tồn tại) → luôn UNREVIEWED. Nay lấy giá qua
PriceSnapshot và verdict từ review mới nhất (ReviewVerdict).

Wave (actual_entry_price):
  - entry_price: giá tham chiếu thesis gốc (immutable, set khi tạo thesis).
  - actual_entry_price: giá vào lệnh thực tế (set bởi /buy, không overwrite khi avg down).
  - format_for_prompt() renders P&L thực tế vs thesis reference khi cả hai đều có.
  - _compute_snapshot() reads actual_entry_price từ ORM object (None nếu chưa có lệnh).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.price_snapshot import (
    STOP_BREACHED,
    STOP_CRITICAL,
    STOP_NEAR,
    PriceSnapshot,
    load_price_snapshots,
)

logger = get_logger(__name__)

# ── tuneable constants ────────────────────────────────────────────────────────
MAX_THESES = 8  # cap to avoid prompt bloat
REVIEW_DUE_DAYS = 7  # days without review → REVIEW_DUE flag
AT_RISK_SCORE_THRESHOLD = 0.35  # health_score ≤ 0.35 → AT_RISK (0.0–1.0 scale)
# Ngưỡng stop: KHÔNG định nghĩa ở đây — dùng thesis.price_snapshot.stop_proximity.
_AT_RISK_PROXIMITIES = frozenset({STOP_BREACHED, STOP_CRITICAL, STOP_NEAR})
_KNOWN_VERDICTS = frozenset(
    {"BULLISH", "BEARISH", "NEUTRAL", "WATCHLIST", "WEAKENING", "INVALIDATED", "INSUFFICIENT_DATA"}
)

# urgency ordering (higher = more urgent, used for sort)
_URGENCY_ORDER = {
    "INVALIDATED": 3,
    "AT_RISK": 2,
    "REVIEW_DUE": 1,
    "OK": 0,
}


@dataclass
class ThesisHealthSnapshot:
    """
    Normalised health snapshot for a single active thesis.

    Fields:
        thesis_id            : UUID string from Thesis.id
        ticker               : e.g. "VCB"
        title                : short thesis title
        direction            : BULLISH | BEARISH | NEUTRAL
        health_score         : 0.0–1.0 (1.0 = perfectly healthy)
        days_since_review    : int — days since last AI review; 999 if never reviewed
        distance_to_stop_pct : % distance from current price to stop_loss;
                               None if stop_loss not set or price unavailable
        assumptions_total    : total assumption count
        assumptions_invalidated : count of invalidated assumptions
        last_verdict         : ReviewVerdict của review mới nhất
                               (BULLISH|BEARISH|NEUTRAL|WATCHLIST|WEAKENING|INVALIDATED|
                               INSUFFICIENT_DATA) hoặc UNREVIEWED
        urgency_flag         : OK | REVIEW_DUE | AT_RISK | INVALIDATED
        stop_loss            : raw stop_loss value (for display), None if not set
        target_price         : raw target_price value, None if not set
        entry_price          : giá tham chiếu thesis gốc, None nếu không set
        actual_entry_price   : giá vào lệnh thực tế (/buy), None nếu chưa có lệnh
        current_price        : giá hiện tại từ PriceSnapshot, None nếu không lấy được
        stop_distance_atr    : (price - stop) / ATR14, None nếu thiếu ATR/stop
        stop_proximity       : FAR | NEAR | CRITICAL | BREACHED | None (rule price_snapshot)
        price_quality        : live | stale | fallback | quote | None
    """

    thesis_id: str
    ticker: str
    title: str
    direction: str
    health_score: float
    days_since_review: int
    distance_to_stop_pct: float | None
    assumptions_total: int
    assumptions_invalidated: int
    last_verdict: str
    urgency_flag: str
    stop_loss: float | None = None
    target_price: float | None = None
    entry_price: float | None = None
    actual_entry_price: float | None = None
    current_price: float | None = None
    stop_distance_atr: float | None = None
    stop_proximity: str | None = None
    price_quality: str | None = None

    @property
    def near_stop(self) -> bool:
        """Sát stop theo rule chung (NEAR hoặc CRITICAL) — chưa xuyên."""
        return self.stop_proximity in (STOP_NEAR, STOP_CRITICAL)

    @property
    def stop_breached(self) -> bool:
        return self.stop_proximity == STOP_BREACHED

    def format_for_prompt(self) -> str:
        """
        Compact, structured prompt line for AI injection.

        Example output (with actual_entry_price):
            [VCB | BULLISH | AT_RISK] "Tăng trưởng CASA" — health=0.32,
            review=12d trước, entry=88000 → actual=85000 (mua thấp hơn 3.4%),
            P&L vs target: +29.4%, stop_loss=80000 (còn 3.2%), target=110000,
            giả định: 2/4 còn valid, verdict: WEAKENING

        Example output (thesis-only, no actual entry):
            [VCB | BULLISH | OK] "Tăng trưởng CASA" — health=0.72,
            review=2d trước, entry=88000, stop_loss=80000 (còn 8.2%),
            target=110000, giả định: 3/4 còn valid, verdict: VALID
        """
        parts: list[str] = []

        # Header
        header = f"[{self.ticker} | {self.direction} | {self.urgency_flag}]"
        title_part = f'"{self.title}"'
        parts.append(f"{header} {title_part}")

        # Health + review
        review_str = (
            f"{self.days_since_review}d trước" if self.days_since_review < 999 else "chưa review"
        )
        details: list[str] = [
            f"health={self.health_score:.2f}",
            f"review={review_str}",
        ]

        # Entry price block — thesis reference vs actual execution
        if self.entry_price is not None and self.actual_entry_price is not None:
            diff_pct = (self.actual_entry_price - self.entry_price) / self.entry_price * 100
            if diff_pct < 0:
                diff_str = f"mua thấp hơn {abs(diff_pct):.1f}%"
            elif diff_pct > 0:
                diff_str = f"mua cao hơn {diff_pct:.1f}%"
            else:
                diff_str = "đúng giá thesis"
            details.append(
                f"entry={self.entry_price:,.0f} → actual={self.actual_entry_price:,.0f} ({diff_str})"
            )
            # P&L vs target from actual entry
            if self.target_price is not None and self.actual_entry_price > 0:
                pnl_to_target = (
                    (self.target_price - self.actual_entry_price) / self.actual_entry_price * 100
                )
                sign = "+" if pnl_to_target >= 0 else ""
                details.append(f"P&L vs target: {sign}{pnl_to_target:.1f}%")
            # P&L vs stop from actual entry
            if self.stop_loss is not None and self.actual_entry_price > 0:
                pnl_to_stop = (
                    (self.stop_loss - self.actual_entry_price) / self.actual_entry_price * 100
                )
                details.append(f"P&L vs stop: {pnl_to_stop:.1f}%")
        elif self.entry_price is not None:
            # Only thesis reference price — no actual execution yet
            details.append(f"entry={self.entry_price:,.0f} (chưa vào lệnh)")

        # Giá hiện tại + chất lượng dữ liệu
        if self.current_price is not None:
            price_str = f"giá={self.current_price:,.0f}"
            if self.price_quality in ("stale", "fallback"):
                price_str += " (dữ liệu cũ)"
            details.append(price_str)

        # Stop-loss proximity — cùng rule với StopBreach/Watchdog
        if self.stop_loss is not None:
            sl_str = f"stop_loss={self.stop_loss:,.0f}"
            if self.stop_breached:
                sl_str += " (ĐÃ XUYÊN)"
            elif self.distance_to_stop_pct is not None:
                sl_str += f" (còn {self.distance_to_stop_pct:.1f}%"
                if self.stop_distance_atr is not None:
                    sl_str += f", {self.stop_distance_atr:.1f} ATR"
                sl_str += ", SÁT STOP)" if self.near_stop else ")"
            details.append(sl_str)

        # Target
        if self.target_price is not None:
            details.append(f"target={self.target_price:,.0f}")

        # Assumptions
        held = self.assumptions_total - self.assumptions_invalidated
        details.append(f"giả định: {held}/{self.assumptions_total} còn valid")

        # Verdict
        details.append(f"verdict: {self.last_verdict}")

        parts.append(" — " + ", ".join(details))
        return "".join(parts)


# ── builder ───────────────────────────────────────────────────────────────────


async def build_thesis_health_snapshots(
    session: AsyncSession,
    user_id: str | None,
    *,
    ticker_context_service: Any | None = None,
    quote_service: Any | None = None,
    max_theses: int = MAX_THESES,
    theses: list[Any] | None = None,
) -> list[ThesisHealthSnapshot]:
    """
    Build ThesisHealthSnapshot list for a user's active theses.

    Args:
        session:  AsyncSession — for ThesisService queries.
        user_id:  target user. Returns [] if None.
        ticker_context_service / quote_service: nguồn giá theo lô (Wave D4).
                  Cả hai None → không có giá, stop_proximity None (không bật AT_RISK theo stop).
        max_theses: cap số thesis trả về (prompt) — consumer briefing có thể nâng.
        theses:   danh sách thesis ACTIVE đã load (reviews/assumptions eager) — truyền
                  vào để không query lần hai; None → tự query qua ThesisService.

    Returns:
        List of ThesisHealthSnapshot sorted by urgency DESC, capped at MAX_THESES.
        Always returns [] on error — never raises.
    """
    if not user_id:
        return []

    if theses is None:
        try:
            from src.thesis.service import ThesisService

            svc = ThesisService(session)
            theses = await svc.list_active(user_id=user_id)
        except Exception as exc:
            logger.warning("thesis_health.list_active_failed", user_id=user_id, error=str(exc))
            return []
    if not theses:
        return []

    prices: dict[str, PriceSnapshot] = {}
    if ticker_context_service is not None or quote_service is not None:
        prices = await load_price_snapshots(
            [t.ticker for t in theses],
            ticker_context_service=ticker_context_service,
            quote_service=quote_service,
            log_event="thesis_health.price_snapshot",
        )

    snapshots: list[ThesisHealthSnapshot] = []
    for thesis in theses:
        try:
            score = await _fetch_score(thesis)
            snap = _compute_snapshot(
                thesis, score, prices.get(str(getattr(thesis, "ticker", "")).upper())
            )
            snapshots.append(snap)
        except Exception as exc:
            logger.warning(
                "thesis_health.snapshot_failed",
                thesis_id=str(getattr(thesis, "id", "?")),
                error=str(exc),
            )
            continue

    # Sort by urgency DESC, then health_score ASC (worst first within same urgency)
    snapshots.sort(key=lambda s: (-_URGENCY_ORDER.get(s.urgency_flag, 0), s.health_score))
    return snapshots[:max_theses]


async def _fetch_score(thesis: object) -> float:
    """Fetch health score for a thesis. Returns 0.5 (neutral) on failure.

    ScoringService.compute() is sync and returns 0.0–100.0.
    Normalized to 0.0–1.0 to match AT_RISK_SCORE_THRESHOLD and
    format_for_prompt() expectations.
    """
    try:
        from src.thesis.scoring_service import ScoringService

        svc = ScoringService()
        raw = svc.compute(thesis)  # type: ignore[arg-type]  # sync 0–100; mypy-baseline M3
        return round(raw / 100.0, 4)  # normalize → 0.0–1.0
    except Exception:
        return 0.5  # neutral fallback — don't penalise for missing score


def _latest_verdict(thesis: object) -> str:
    """ReviewVerdict của review mới nhất (theo reviewed_at), UNREVIEWED nếu chưa có."""
    reviews = getattr(thesis, "reviews", None) or []
    latest = None
    for r in reviews:
        ts = getattr(r, "reviewed_at", None) or getattr(r, "created_at", None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if latest is None or ts > latest[0]:
            latest = (ts, r)
    if latest is None:
        return "UNREVIEWED"
    raw = getattr(latest[1], "verdict", None)
    verdict = str(getattr(raw, "value", raw) or "").upper()
    return verdict if verdict in _KNOWN_VERDICTS else "UNREVIEWED"


def _compute_snapshot(
    thesis: object,
    health_score: float,
    price: PriceSnapshot | None = None,
) -> ThesisHealthSnapshot:
    """Compute ThesisHealthSnapshot from a thesis ORM object + health score + giá."""
    thesis_id = str(getattr(thesis, "id", ""))
    ticker = str(getattr(thesis, "ticker", ""))
    title = str(getattr(thesis, "title", "") or "")
    raw_dir = getattr(thesis, "direction", None) or "BULLISH"
    direction = str(getattr(raw_dir, "value", raw_dir)).upper()

    # Price fields
    stop_loss: float | None = getattr(thesis, "stop_loss", None)
    target_price: float | None = getattr(thesis, "target_price", None)
    entry_price: float | None = getattr(thesis, "entry_price", None)
    actual_entry_price: float | None = getattr(thesis, "actual_entry_price", None)

    # Wave D4: giá + khoảng cách stop từ PriceSnapshot (rule chung thesis segment)
    current_price: float | None = price.price if price else None
    distance_to_stop_pct = price.stop_distance_pct(stop_loss) if price else None
    stop_distance_atr = price.stop_distance_atr(stop_loss) if price else None
    proximity = price.stop_proximity(stop_loss) if price else None

    # Days since last review
    last_reviewed_at = getattr(thesis, "last_reviewed_at", None)
    if last_reviewed_at is not None:
        now = datetime.now(UTC)
        if last_reviewed_at.tzinfo is None:
            last_reviewed_at = last_reviewed_at.replace(tzinfo=UTC)
        days_since_review = max(0, (now - last_reviewed_at).days)
    else:
        days_since_review = 999  # sentinel: never reviewed

    # Assumptions
    assumptions = getattr(thesis, "assumptions", []) or []
    assumptions_total = len(assumptions)
    # AssumptionStatus.INVALID == "invalid" (trước D4 so với "invalidated" → luôn 0)
    assumptions_invalidated = sum(
        1
        for a in assumptions
        if str(getattr(getattr(a, "status", ""), "value", getattr(a, "status", ""))).lower()
        in ("invalid", "invalidated")
    )

    last_verdict = _latest_verdict(thesis)

    # Status guard — if somehow invalidated thesis sneaks in
    raw_status = getattr(thesis, "status", "active")
    status = str(getattr(raw_status, "value", raw_status) or "active").lower()
    if status == "invalidated":
        urgency_flag = "INVALIDATED"
    elif proximity in _AT_RISK_PROXIMITIES or health_score <= AT_RISK_SCORE_THRESHOLD:
        urgency_flag = "AT_RISK"
    elif days_since_review >= REVIEW_DUE_DAYS:
        urgency_flag = "REVIEW_DUE"
    else:
        urgency_flag = "OK"

    return ThesisHealthSnapshot(
        thesis_id=thesis_id,
        ticker=ticker,
        title=title,
        direction=direction,
        health_score=health_score,
        days_since_review=days_since_review,
        distance_to_stop_pct=distance_to_stop_pct,
        assumptions_total=assumptions_total,
        assumptions_invalidated=assumptions_invalidated,
        last_verdict=last_verdict,
        urgency_flag=urgency_flag,
        stop_loss=stop_loss,
        target_price=target_price,
        entry_price=entry_price,
        actual_entry_price=actual_entry_price,
        current_price=current_price,
        stop_distance_atr=stop_distance_atr,
        stop_proximity=proximity,
        price_quality=price.source_quality if price else None,
    )
