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
  build_thesis_health_snapshots(session, user_id)
    └─ ThesisService.list_active()          → list of ORM thesis objects
    └─ ScoringService.compute(thesis)       → float 0.0–100.0, normalized to 0.0–1.0
    └─ _compute_snapshot(thesis, score)     → ThesisHealthSnapshot
    └─ sort by urgency DESC, cap at MAX_THESES

urgency_flag priority order (highest → lowest):
  INVALIDATED  → thesis.status == "invalidated" (should not appear in active list
                 but guard anyway)
  AT_RISK      → distance_to_stop_pct is not None and <= AT_RISK_STOP_PCT_THRESHOLD
                 OR health_score <= AT_RISK_SCORE_THRESHOLD
  REVIEW_DUE   → days_since_review >= REVIEW_DUE_DAYS
  OK           → everything else
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger

logger = get_logger(__name__)

# ── tuneable constants ────────────────────────────────────────────────────────
MAX_THESES = 8               # cap to avoid prompt bloat
REVIEW_DUE_DAYS = 7         # days without review → REVIEW_DUE flag
AT_RISK_STOP_PCT_THRESHOLD = 5.0   # ≤5% from stop_loss → AT_RISK
AT_RISK_SCORE_THRESHOLD = 0.35     # health_score ≤ 0.35 → AT_RISK (0.0–1.0 scale)

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
        direction            : LONG | SHORT | WATCH
        health_score         : 0.0–1.0 (1.0 = perfectly healthy)
        days_since_review    : int — days since last AI review; 999 if never reviewed
        distance_to_stop_pct : % distance from current price to stop_loss;
                               None if stop_loss not set or price unavailable
        assumptions_total    : total assumption count
        assumptions_invalidated : count of invalidated assumptions
        last_verdict         : VALID | WEAKENING | INVALID | UNREVIEWED
        urgency_flag         : OK | REVIEW_DUE | AT_RISK | INVALIDATED
        stop_loss            : raw stop_loss value (for display), None if not set
        target_price         : raw target_price value, None if not set
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

    def format_for_prompt(self) -> str:
        """
        Compact, structured prompt line for AI injection.

        Example output:
            [VCB | LONG | AT_RISK] "Tăng trưởng CASA" — health=0.32,
            review=12d trước, stop_loss=88000 (còn 3.2%), target=110000,
            giả định: 2/4 còn valid, verdict: WEAKENING
        """
        parts: list[str] = []

        # Header
        header = f"[{self.ticker} | {self.direction} | {self.urgency_flag}]"
        title_part = f'"{self.title}"'
        parts.append(f"{header} {title_part}")

        # Health + review
        review_str = (
            f"{self.days_since_review}d trước"
            if self.days_since_review < 999
            else "chưa review"
        )
        details: list[str] = [
            f"health={self.health_score:.2f}",
            f"review={review_str}",
        ]

        # Stop-loss proximity
        if self.stop_loss is not None:
            sl_str = f"stop_loss={self.stop_loss:,.0f}"
            if self.distance_to_stop_pct is not None:
                sl_str += f" (còn {self.distance_to_stop_pct:.1f}%)"
            details.append(sl_str)

        # Target
        if self.target_price is not None:
            details.append(f"target={self.target_price:,.0f}")

        # Assumptions
        held = self.assumptions_total - self.assumptions_invalidated
        details.append(
            f"giả định: {held}/{self.assumptions_total} còn valid"
        )

        # Verdict
        details.append(f"verdict: {self.last_verdict}")

        parts.append(" — " + ", ".join(details))
        return "".join(parts)


# ── builder ───────────────────────────────────────────────────────────────────

async def build_thesis_health_snapshots(
    session: "AsyncSession",
    user_id: str | None,
) -> list[ThesisHealthSnapshot]:
    """
    Build ThesisHealthSnapshot list for a user's active theses.

    Args:
        session:  AsyncSession — for ThesisService queries.
        user_id:  target user. Returns [] if None.

    Returns:
        List of ThesisHealthSnapshot sorted by urgency DESC, capped at MAX_THESES.
        Always returns [] on error — never raises.
    """
    if not user_id:
        return []

    try:
        from src.thesis.service import ThesisService

        svc = ThesisService(session)
        theses = await svc.list_active(user_id=user_id)
        if not theses:
            return []
    except Exception as exc:
        logger.warning("thesis_health.list_active_failed", user_id=user_id, error=str(exc))
        return []

    snapshots: list[ThesisHealthSnapshot] = []
    for thesis in theses:
        try:
            score = await _fetch_score(thesis)
            snap = _compute_snapshot(thesis, score)
            snapshots.append(snap)
        except Exception as exc:
            logger.warning(
                "thesis_health.snapshot_failed",
                thesis_id=str(getattr(thesis, "id", "?")),
                error=str(exc),
            )
            continue

    # Sort by urgency DESC, then health_score ASC (worst first within same urgency)
    snapshots.sort(
        key=lambda s: (-_URGENCY_ORDER.get(s.urgency_flag, 0), s.health_score)
    )
    return snapshots[:MAX_THESES]


async def _fetch_score(thesis: object) -> float:
    """Fetch health score for a thesis. Returns 0.5 (neutral) on failure.

    ScoringService.compute() is sync and returns 0.0–100.0.
    Normalized to 0.0–1.0 to match AT_RISK_SCORE_THRESHOLD and
    format_for_prompt() expectations.
    """
    try:
        from src.thesis.scoring_service import ScoringService

        svc = ScoringService()
        raw = svc.compute(thesis)      # sync, returns 0.0–100.0
        return round(raw / 100.0, 4)   # normalize → 0.0–1.0
    except Exception:
        return 0.5  # neutral fallback — don't penalise for missing score


def _compute_snapshot(thesis: object, health_score: float) -> ThesisHealthSnapshot:
    """Compute ThesisHealthSnapshot from a thesis ORM object + health score."""
    thesis_id = str(getattr(thesis, "id", ""))
    ticker = str(getattr(thesis, "ticker", ""))
    title = str(getattr(thesis, "title", "") or "")
    direction = str(getattr(thesis, "direction", "LONG") or "LONG").upper()

    # Stop-loss + target
    stop_loss: float | None = getattr(thesis, "stop_loss", None)
    target_price: float | None = getattr(thesis, "target_price", None)
    current_price: float | None = getattr(thesis, "current_price", None)

    # Distance to stop as % of current price
    distance_to_stop_pct: float | None = None
    if stop_loss is not None and current_price and current_price > 0:
        distance_to_stop_pct = abs((current_price - stop_loss) / current_price * 100)

    # Days since last review
    last_reviewed_at = getattr(thesis, "last_reviewed_at", None)
    if last_reviewed_at is not None:
        now = datetime.now(timezone.utc)
        if last_reviewed_at.tzinfo is None:
            last_reviewed_at = last_reviewed_at.replace(tzinfo=timezone.utc)
        days_since_review = max(0, (now - last_reviewed_at).days)
    else:
        days_since_review = 999  # sentinel: never reviewed

    # Assumptions
    assumptions = getattr(thesis, "assumptions", []) or []
    assumptions_total = len(assumptions)
    assumptions_invalidated = sum(
        1 for a in assumptions
        if str(getattr(a, "status", "")).lower() in ("invalidated", "false", "failed")
    )

    # Last verdict from thesis object or default
    last_verdict_raw = (
        getattr(thesis, "last_verdict", None)
        or getattr(thesis, "verdict", None)
        or "UNREVIEWED"
    )
    last_verdict = str(last_verdict_raw).upper()
    if last_verdict not in ("VALID", "WEAKENING", "INVALID", "UNREVIEWED"):
        last_verdict = "UNREVIEWED"

    # Status guard — if somehow invalidated thesis sneaks in
    status = str(getattr(thesis, "status", "active")).lower()
    if status == "invalidated":
        urgency_flag = "INVALIDATED"
    elif (
        (distance_to_stop_pct is not None and distance_to_stop_pct <= AT_RISK_STOP_PCT_THRESHOLD)
        or health_score <= AT_RISK_SCORE_THRESHOLD
    ):
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
    )
