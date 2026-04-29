"""Timeline parser — converts AI free-form string → datetime.

Owner: thesis segment.
Pure utility: no ORM, no DB, no external deps beyond stdlib.
Import trực tiếp ở bất kỳ đâu cần parse timeline string từ AI output.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime

from src.platform.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

_MONTH_MAP: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4,
    "JUNE": 6, "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9,
    "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}

_Q_END_MONTH: dict[int, int] = {1: 3, 2: 6, 3: 9, 4: 12}
_Q_END_DAY: dict[int, int] = {3: 31, 6: 30, 9: 30, 12: 31}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_timeline_to_date(timeline: str | None) -> datetime | None:
    """Convert AI free-form timeline string → end-of-period datetime (UTC).

    Supported patterns (case-insensitive):
      "Q3 2026"          → 2026-09-30
      "Q4/2026"          → 2026-12-31
      "H1 2026"          → 2026-06-30
      "H2 2026"          → 2026-12-31
      "tháng 6 2026"     → 2026-06-30
      "06/2026"          → 2026-06-30
      "June 2026"        → 2026-06-30
      "cuối năm 2026"    → 2026-12-31
      "end of 2026"      → 2026-12-31
      "2026"             → 2026-12-31  (fallback)

    Returns None if no pattern matches.
    """
    if not timeline:
        return None
    t = timeline.strip().upper()

    def _eom(year: int, month: int) -> datetime:
        last = calendar.monthrange(year, month)[1]
        return datetime(year, month, last, tzinfo=UTC)

    # Q1-Q4 YYYY  (separator: space, /, -)
    m = re.search(r"Q([1-4])[\s/\-]*(\d{4})", t)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        em = _Q_END_MONTH[q]
        return datetime(y, em, _Q_END_DAY[em], tzinfo=UTC)

    # H1 / H2 YYYY
    m = re.search(r"H([12])[\s/\-]*(\d{4})", t)
    if m:
        h, y = int(m.group(1)), int(m.group(2))
        return _eom(y, 6 if h == 1 else 12)

    # "THÁNG 6 2026" or "THÁNG6/2026"
    m = re.search(r"THÁNG\s*(\d{1,2})[\s/\-]*(\d{4})", t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return _eom(y, mo)

    # "June 2026" / "Jun 2026"
    for name, mo in _MONTH_MAP.items():
        m = re.search(rf"\b{name}\b[\s/\-]*(\d{{4}})", t)
        if m:
            return _eom(int(m.group(1)), mo)

    # "06/2026" or "6-2026"
    m = re.search(r"\b(\d{1,2})[/\-](\d{4})\b", t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return _eom(y, mo)

    # "cuối năm 2026" / "end of year 2026" / "end 2026"
    m = re.search(r"(?:CUỐI\s*NĂM|END\s*OF\s*YEAR?|END)\s*(\d{4})", t)
    if m:
        return datetime(int(m.group(1)), 12, 31, tzinfo=UTC)

    # Bare year fallback: "2026"
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        return datetime(int(m.group(1)), 12, 31, tzinfo=UTC)

    logger.warning("parse_timeline_to_date.unmatched", raw=timeline)
    return None
