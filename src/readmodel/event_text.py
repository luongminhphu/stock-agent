"""event_text.py — Smart flattener cho TimelineEvent.detail → human-readable text.

Owner: readmodel segment.
Pure utility: không import ORM, không gọi DB, không có side-effect.

Hai public API:
  flatten_detail(detail)  → str  (single event detail → text)
  filter_events(events)   → list[TimelineEvent]  (lọc null/rỗng + giữ 30 gần nhất, newest→oldest)
"""

from __future__ import annotations

import math
from typing import Any

from src.readmodel.schemas import TimelineEvent

# ---------------------------------------------------------------------------
# Field labels — ưu tiên hiển thị nếu key khớp
# ---------------------------------------------------------------------------

_LABEL_MAP: dict[str, str] = {
    "verdict":              "Verdict",
    "confidence":           "Confidence",
    "risk_signals":         "Rủi ro",
    "next_watch_items":     "Cần theo dõi",
    "entry_price":          "Giá vào",
    "target_price":         "Mục tiêu",
    "stop_loss":            "Stop loss",
    "score":                "Score",
    "pnl_pct":              "P&L",
    "price":                "Giá",
    "final_score":          "Score cuối",
    "status":               "Trạng thái",
    "assumption_id":        "Assumption",
    "catalyst_id":          "Catalyst",
    "assumption_health":    "Assumption health",
    "catalyst_progress":    "Catalyst progress",
    "risk_reward":          "Risk/reward",
    "review_confidence":    "AI confidence",
}

# ---------------------------------------------------------------------------
# Formatter helpers
# ---------------------------------------------------------------------------


def _is_empty(v: Any) -> bool:
    """True nếu value không mang thông tin hữu ích."""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def _fmt_value(key: str, v: Any) -> str:
    """Format một value thành chuỗi đẹp dựa trên key hint."""
    if isinstance(v, float):
        if key in ("confidence", "pnl_pct", "assumption_health",
                   "catalyst_progress", "risk_reward", "review_confidence"):
            pct = v * 100 if key == "confidence" and 0 <= v <= 1 else v
            sign = "+" if pct > 0 and key == "pnl_pct" else ""
            return f"{sign}{pct:.1f}%"
        if key in ("entry_price", "target_price", "stop_loss", "price") and v > 100:
            return f"{v:,.0f} ₫"
        if key in ("score", "final_score") and 0 <= v <= 100:
            return f"{v:.1f}"
        return f"{v:.2f}"

    if isinstance(v, int):
        return str(v)

    if isinstance(v, list):
        cleaned = [str(x).strip() for x in v if not _is_empty(x)]
        if not cleaned:
            return ""
        return " • ".join(cleaned)

    if isinstance(v, dict):
        parts = []
        for sub_k, sub_v in v.items():
            if _is_empty(sub_v):
                continue
            label = _LABEL_MAP.get(sub_k, sub_k.replace("_", " ").capitalize())
            parts.append(f"{label}: {_fmt_value(sub_k, sub_v)}")
        return ", ".join(parts) if parts else ""

    return str(v).strip()


# ---------------------------------------------------------------------------
# Public: flatten_detail
# ---------------------------------------------------------------------------


def flatten_detail(detail: dict | None) -> str:
    """Convert TimelineEvent.detail dict → human-readable single string.

    - Bỏ qua keys có value null / rỗng / NaN / list rỗng / dict rỗng.
    - Nested dict được flatten đệ quy (tối đa 2 tầng).
    - List được join bằng " • ".
    - Ưu tiên label tiếng Việt nếu key khớp _LABEL_MAP.

    Returns "" nếu detail None hoặc toàn bộ keys đều rỗng.
    """
    if not detail or not isinstance(detail, dict):
        return ""

    parts: list[str] = []
    for key, val in detail.items():
        if _is_empty(val):
            continue
        formatted = _fmt_value(key, val)
        if not formatted:
            continue
        label = _LABEL_MAP.get(key, key.replace("_", " ").capitalize())
        parts.append(f"{label}: {formatted}")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Public: filter_events
# ---------------------------------------------------------------------------

_MAX_EVENTS = 30


def _event_is_meaningful(event: TimelineEvent) -> bool:
    """True nếu event có ít nhất summary hoặc detail không rỗng."""
    has_summary = bool(event.summary and event.summary.strip())
    has_detail = bool(event.detail and any(
        not _is_empty(v) for v in event.detail.values()
    ))
    return has_summary or has_detail


def filter_events(events: list[TimelineEvent]) -> list[TimelineEvent]:
    """Lọc và giữ tối đa 30 events gần nhất, sắp xếp newest → oldest.

    Pipeline:
      1. Bỏ events null/rỗng (summary rỗng VÀ detail rỗng/null).
      2. Sort descending by ts → newest event ở index 0.
      3. Giữ 30 event đầu (gần nhất theo thời gian).

    Input list không bị mutate. Output luôn newest → oldest.
    """
    meaningful = [e for e in events if _event_is_meaningful(e)]
    return sorted(meaningful, key=lambda e: e.ts, reverse=True)[:_MAX_EVENTS]
