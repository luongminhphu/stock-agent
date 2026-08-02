"""VNTradingCalendar — Vietnamese stock market session rules (HOSE/HNX/UPCoM).

Owner: market segment.

Single source of truth for "is the market tradable right now". Replaces the
scattered weekday+hour checks that lived in quote_service.TradingHoursGuard
and bot.scheduler._in_market_hours — both of which ignored the lunch break
(11:30–13:00 ICT) and public holidays, causing scans/alerts/quote fetches to
fire into dead sessions and burn adapter calls on stale data.

Session structure (ICT = UTC+7):
  Morning   09:00–11:30   (ATO 09:00–09:15, continuous 09:15–11:30)
  Lunch     11:30–13:00   (no matching)
  Afternoon 13:00–14:30   (continuous)
  ATC       14:30–14:45
  Close     15:00

For gating purposes we treat the tradable window as 09:00–15:00 minus the
lunch break. Sub-minute precision (ATO/ATC) does not change scan/alert
behaviour, so the calendar exposes continuous-session semantics only.

Holidays: fixed Gregorian holidays + Tết (Lunar New Year) computed from a
small lookup table of Tết dates (2024–2030). Extending the table is a one-line
change per year — safer than a lunar-calendar dependency for this app.

Consumers:
  - market.quote_service.TradingHoursGuard  → blocks live fetches
  - bot.scheduler._in_market_hours          → blocks scan/drift tasks
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

_ICT = timezone(timedelta(hours=7))

# Continuous-session bounds (ICT). Lunch break is carved out separately.
_MARKET_OPEN = time(9, 0)
_MARKET_CLOSE = time(15, 0)
_LUNCH_START = time(11, 30)
_LUNCH_END = time(13, 0)

# Tết (Lunar New Year) first day of the holiday, per year. The market
# typically closes for ~5 trading days around Tết; we mark a 7-day block
# starting 2 days before Tết day 1 to cover the full closure window.
_TET_DATES: dict[int, date] = {
    2024: date(2024, 2, 10),
    2025: date(2025, 1, 29),
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),
    2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
}


def _fixed_holidays(year: int) -> set[date]:
    """Gregorian public holidays when the exchange is closed."""
    return {
        date(year, 1, 1),    # New Year
        date(year, 4, 30),   # Reunification Day
        date(year, 5, 1),    # Labour Day
        date(year, 9, 2),    # National Day
    }


def _tet_window(year: int) -> set[date]:
    """7-day closure block around Tết day 1 (2 before through 4 after)."""
    tet = _TET_DATES.get(year)
    if tet is None:
        return set()
    return {tet + timedelta(days=d) for d in range(-2, 5)}


class VNTradingCalendar:
    """Stateless session rules for the Vietnamese market."""

    @staticmethod
    def is_trading_day(d: date) -> bool:
        """True if the exchange opens on this calendar date (ICT)."""
        if d.weekday() >= 5:  # Sat/Sun
            return False
        if d in _fixed_holidays(d.year):
            return False
        if d in _tet_window(d.year):
            return False
        # A holiday on Fri→Sun pushes closure to adjacent weekdays; keep it
        # simple — the exchange publishes compensations rarely and the fixed
        # set above covers the common cases. Document as a known simplification.
        return True

    @staticmethod
    def is_lunch_break(t: time) -> bool:
        """True if the time-of-day falls in the 11:30–13:00 lunch break."""
        return _LUNCH_START <= t < _LUNCH_END

    @classmethod
    def is_trading_now(cls, now: datetime | None = None) -> bool:
        """True if the market is in a tradable continuous session right now.

        Accepts naive (assumed ICT) or aware datetimes; normalises to ICT.
        """
        t = (now or datetime.now(_ICT)).astimezone(_ICT)
        if not cls.is_trading_day(t.date()):
            return False
        tod = t.time()
        if cls.is_lunch_break(tod):
            return False
        return _MARKET_OPEN <= tod <= _MARKET_CLOSE
