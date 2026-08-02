"""Watchlist embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (WatchlistScanScheduler).
"""

from __future__ import annotations

import datetime

import discord

from src.bot.discord_helper import COLORS, fmt_ict


def _price_icon(change_pct: float, has_alerts: bool) -> str:
    """Return directional color circle, or bell when alert is active."""
    if has_alerts:
        return "\U0001f514"  # 🔔
    return "\U0001f7e2" if change_pct >= 0 else "\U0001f534"  # 🟢 / 🔴


def _dominant_color(signals: list) -> int:
    """Return embed sidebar color based on majority direction of signals."""
    if not signals:
        return COLORS.TEAL
    ups   = sum(1 for s in signals if s.change_pct >= 0)
    downs = len(signals) - ups
    if ups > downs:
        return COLORS.GREEN
    if downs > ups:
        return COLORS.RED
    return COLORS.ORANGE


def _thesis_badge(thesis_score: float | None) -> str:
    """Return a short thesis health badge for display in scan lines.

    Mapping (aligned with ScoringService tiers):
      score < 30  → "(thesis Critical)"
      30 ≤ score < 50 → "(thesis Weak)"
      otherwise  → "" (no badge)
    """
    if thesis_score is None:
        return ""
    try:
        score = float(thesis_score)
    except (TypeError, ValueError):  # defensive: ignore bad input
        return ""
    if score < 30:
        return " (thesis Critical)"
    if score < 50:
        return " (thesis Weak)"
    return ""


def _signal_priority(s: object) -> float:
    """Rank a scan signal for the digest: alerts first, then |move|."""
    alert_bonus = 100.0 if getattr(s, "has_alerts", False) else 0.0
    return alert_bonus + abs(getattr(s, "change_pct", 0.0))


def build_scan_embed(
    result: object,
    now_utc: datetime.datetime,
    *,
    top_n: int = 5,
) -> discord.Embed:
    """Build embed for WatchlistScanScheduler periodic scan notification.

    Wave 4b digest: when more than top_n signals fire in one scan tick
    (typical at open/close bursts), show the top_n most important
    (alert-triggered first, then biggest |change_pct|) and collapse the
    rest into a single summary line instead of a wall of tickers.
    top_n <= 0 disables the digest (legacy behaviour, all signals shown).
    """
    signals = getattr(result, "signals", []) or []
    on_signal_reminders = getattr(result, "on_signal_reminders", []) or []

    if top_n > 0 and len(signals) > top_n:
        ranked = sorted(signals, key=_signal_priority, reverse=True)
        shown, overflow = ranked[:top_n], ranked[top_n:]
    else:
        shown, overflow = signals, []

    lines: list[str] = []
    for s in shown:
        icon = _price_icon(s.change_pct, s.has_alerts)
        thesis_badge = _thesis_badge(getattr(s, "thesis_score", None))
        lines.append(
            f"{icon} **{s.ticker}** {s.change_pct:+.1f}% — {s.description}{thesis_badge}"
        )

    for r in on_signal_reminders:
        ticker = (
            r.watchlist_item.ticker
            if r.watchlist_item
            else f"item#{r.watchlist_item_id}"
        )
        lines.append(f"\u23f0 **{ticker}** \u2014 nh\u1eafc nh\u1edf theo d\u00f5i (ON_SIGNAL)")

    if overflow:
        overflow_tickers = ", ".join(s.ticker for s in overflow)
        n_alerts_overflow = sum(len(getattr(s, "triggered_alerts", []) or []) for s in overflow)
        alert_note = f" (gồm {n_alerts_overflow} alert)" if n_alerts_overflow else ""
        lines.append(
            f"\u2026 và **{len(overflow)}** t\u00edn hi\u1ec7u kh\u00e1c{alert_note}: {overflow_tickers}"
        )

    embed = discord.Embed(
        title="\U0001f4e1 Watchlist Scan",
        description="\n".join(lines),
        color=_dominant_color(signals),
    )

    signal_count = len(signals)
    reminder_count = len(on_signal_reminders)
    footer_parts = [f"Scan l\u00fac {fmt_ict(now_utc, fmt='%H:%M ICT')}"]
    if signal_count:
        footer_parts.append(f"{signal_count} t\u00edn hi\u1ec7u")
    if reminder_count:
        footer_parts.append(f"{reminder_count} nh\u1eafc nh\u1edf")
    embed.set_footer(text=" \u2014 ".join(footer_parts))
    return embed
