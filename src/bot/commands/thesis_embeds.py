"""Thesis embed builders and display constants.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by thesis_crud.py, thesis_review.py, and scheduler.py.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

import discord

from src.bot.discord_helper import (
    COLORS,
    STATUS_ICONS,
    VERDICT_ICONS,
    confidence_bar,
    fmt_ict,
    truncate,
)
from src.thesis import ReviewVerdict, ThesisStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

# Use COLORS.* int constants — not discord.Color.* — for consistency with
# all other embeds in the codebase (discord.Embed accepts int directly).
_VERDICT_COLOUR: dict[ReviewVerdict, int] = {
    ReviewVerdict.BULLISH: COLORS.GREEN,
    ReviewVerdict.BEARISH: COLORS.RED,
    ReviewVerdict.WEAKENING: COLORS.ORANGE,  # added
    ReviewVerdict.NEUTRAL: COLORS.TEAL,
    ReviewVerdict.INVALIDATED: COLORS.RED,  # added
    ReviewVerdict.WATCHLIST: COLORS.BLUE,
}

_VERDICT_ICON: dict[ReviewVerdict, str] = {
    ReviewVerdict.BULLISH: VERDICT_ICONS["BULLISH"],
    ReviewVerdict.BEARISH: VERDICT_ICONS["BEARISH"],
    ReviewVerdict.WEAKENING: VERDICT_ICONS["WEAKENING"],  # added
    ReviewVerdict.NEUTRAL: VERDICT_ICONS["NEUTRAL"],
    ReviewVerdict.INVALIDATED: VERDICT_ICONS["INVALIDATED"],  # added
    ReviewVerdict.WATCHLIST: VERDICT_ICONS["WATCHLIST"],
}

STATUS_ICON: dict[ThesisStatus, str] = {
    ThesisStatus.ACTIVE: STATUS_ICONS["ACTIVE"],
    ThesisStatus.PAUSED: STATUS_ICONS["PAUSED"],
    ThesisStatus.WEAKENING: STATUS_ICONS["WEAKENING"],  # added
    ThesisStatus.INVALIDATED: STATUS_ICONS["INVALIDATED"],
    ThesisStatus.CLOSED: STATUS_ICONS["CLOSED"],
}

TARGET_ICON: dict[str, str] = {
    "assumption": "\U0001f4cc",  # 📌
    "catalyst": "\u26a1",  # ⚡
}


def _parse_json_list(raw: str | list[Any] | None) -> list[Any]:
    """Safely parse a JSON-encoded list or return the value if already a list.

    ORM columns may store lists as JSON strings. This adapter normalises both
    representations so embed builders never need to care about storage format.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.debug("thesis_embeds._parse_json_list: failed to parse %r", raw)
        return []


def _dominant_verdict_color(reviews: list[Any]) -> int:
    """Derive sidebar color from dominant verdict in a list of ThesisReview objects."""
    bullish = sum(1 for r in reviews if str(r.verdict).upper() == "BULLISH")
    bearish = sum(1 for r in reviews if str(r.verdict).upper() == "BEARISH")
    if bullish > bearish:
        return COLORS.GREEN
    if bearish > bullish:
        return COLORS.RED
    return COLORS.TEAL  # neutral/mixed → default info color


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def build_review_embed(review: object) -> discord.Embed:
    """Build a rich embed from a ThesisReview ORM object.

    Handles ORM storage quirks:
    - risk_signals / next_watch_items may be JSON strings or Python lists.
    - reviewed_price may be None.
    - verdict may be a ReviewVerdict enum or a plain string.
    """
    # Normalise verdict — support both enum and string (e.g. from AI output)
    raw_verdict = getattr(review, "verdict", None)
    try:
        verdict = ReviewVerdict(str(raw_verdict or ""))
    except (ValueError, KeyError):
        verdict = ReviewVerdict.NEUTRAL

    colour = _VERDICT_COLOUR.get(verdict, COLORS.GREY)
    icon = _VERDICT_ICON.get(verdict, "\u26aa")

    embed = discord.Embed(
        title=f"{icon} Thesis #{getattr(review, 'thesis_id', '?')} \u2014 {verdict.value}",
        description=truncate(getattr(review, "reasoning", "") or "", 1000),
        colour=colour,
    )

    confidence = float(getattr(review, "confidence", 0.0) or 0.0)
    embed.add_field(
        name="Độ tin cậy",
        value=f"{confidence_bar(confidence)} `{confidence:.0%}`",
        inline=False,
    )

    risks = _parse_json_list(getattr(review, "risk_signals", None))
    if risks:
        embed.add_field(
            name="\u26a0\ufe0f Risk Signals",
            value="\n".join(f"\u2022 {r}" for r in risks[:5]),
            inline=False,
        )

    watches = _parse_json_list(getattr(review, "next_watch_items", None))
    if watches:
        embed.add_field(
            name="\U0001f441\ufe0f Watch Next",
            value="\n".join(f"\u2022 {w}" for w in watches[:5]),
            inline=False,
        )

    reviewed_price = getattr(review, "reviewed_price", None)
    price_str = f"{reviewed_price:,.0f} VND" if reviewed_price else "N/A"
    reviewed_at = getattr(review, "reviewed_at", None)
    ts_str = fmt_ict(reviewed_at) if reviewed_at else "N/A"
    embed.set_footer(text=f"Price at review: {price_str} \u00b7 {ts_str} \u00b7 stock-agent")
    return embed


def build_maintenance_embed(
    expired_count: int,
    reviews: list[Any],
    now_utc: datetime.datetime,
    upcoming_catalysts: list[dict[str, Any]] | None = None,
    catalyst_lookahead_days: int = 30,
) -> discord.Embed:
    """Build embed for ThesisMaintenanceScheduler daily summary."""
    lines: list[str] = []
    if expired_count:
        lines.append(
            f"\u23f0 **{expired_count}** catalyst \u0111\u00e3 h\u1ebft h\u1ea1n \u2192 EXPIRED"
        )
    for r in reviews:
        try:
            verdict_enum = ReviewVerdict(r.verdict)
            icon = _VERDICT_ICON.get(verdict_enum, "\u26aa")
        except (ValueError, KeyError):
            icon = "\u26aa"
        lines.append(
            f"{icon} Thesis #{r.thesis_id} \u2014 {r.verdict} (confidence: {r.confidence:.0%})"
        )

    embed = discord.Embed(
        title="\U0001f527 Thesis Maintenance",
        description="\n".join(lines) if lines else None,
        color=_dominant_verdict_color(reviews) if reviews else COLORS.TEAL,
    )

    if upcoming_catalysts:
        today = now_utc.date()
        urgent_threshold = today + datetime.timedelta(days=3)
        urgent_lines: list[str] = []
        upcoming_lines: list[str] = []

        for c in upcoming_catalysts:
            ticker = c.get("ticker", "?")
            description = c.get("description") or c.get("name") or "Catalyst"
            raw_date = c.get("expected_date")

            if raw_date is None:
                date_str = "?"
                cat_date = None
            elif isinstance(raw_date, datetime.datetime):
                cat_date = raw_date.date()
                date_str = cat_date.strftime("%d/%m/%Y")
            elif isinstance(raw_date, datetime.date):
                cat_date = raw_date
                date_str = cat_date.strftime("%d/%m/%Y")
            else:
                try:
                    cat_date = datetime.date.fromisoformat(str(raw_date)[:10])
                    date_str = cat_date.strftime("%d/%m/%Y")
                except ValueError:
                    cat_date = None
                    date_str = str(raw_date)[:10]

            if cat_date is not None and cat_date <= urgent_threshold:
                days_left = (cat_date - today).days
                days_str = f"còn **{days_left} ngày**" if days_left > 0 else "**hôm nay**"
                urgent_lines.append(
                    f"\u26a1 **{ticker}** — {description} `{date_str}` ({days_str})"
                )
            else:
                upcoming_lines.append(f"\U0001f4c5 **{ticker}** — {description} `{date_str}`")

        catalyst_field_lines: list[str] = []
        if urgent_lines:
            catalyst_field_lines.extend(urgent_lines)
        if upcoming_lines:
            if urgent_lines:
                catalyst_field_lines.append("")
            catalyst_field_lines.extend(upcoming_lines)

        if catalyst_field_lines:
            embed.add_field(
                name=f"\U0001f4c5 Catalyst sắp đến ({catalyst_lookahead_days} ngày tới)",
                value=truncate("\n".join(catalyst_field_lines), 1024),
                inline=False,
            )

    embed.set_footer(text=f"Auto-maintenance lúc {fmt_ict(now_utc, fmt='%H:%M ICT')} · stock-agent")
    return embed


# ---------------------------------------------------------------------------
# Wave D3: Watchdog embeds — render WatchdogRunResult (thesis.watchdog_service)
# ---------------------------------------------------------------------------


# ── Kế hoạch nắm giữ — bản tổng hợp sau đóng cửa ────────────────────────────

_PLAN_LIGHT_ICON = {"do": "🔴", "vang": "🟡", "xanh": "🟢", "xam": "⚪"}
_PLAN_LIGHT_NAME = {
    "do": "Kế hoạch đã sai",
    "vang": "Cần xem lại",
    "xanh": "Đúng kế hoạch",
    "xam": "Đã đóng",
}
_PLAN_COLOUR = {"do": 0xDC2626, "vang": 0xD97706, "xanh": 0x16A34A}


def build_plan_digest_embed(changes: list[Any], now_utc: datetime.datetime) -> discord.Embed:
    """Một embed cho mọi kế hoạch đổi đèn hôm nay — đỏ trước, rồi vàng, rồi xanh."""
    order = {"do": 0, "vang": 1, "xanh": 2, "xam": 3}
    changes = sorted(changes, key=lambda c: (order.get(c.new_light, 9), c.ticker))
    worst = changes[0].new_light if changes else "xanh"
    embed = discord.Embed(
        title="📋 Kiểm tra kế hoạch nắm giữ",
        description="Các kế hoạch vừa đổi trạng thái sau phiên hôm nay.",
        colour=_PLAN_COLOUR.get(worst, 0x64748B),
        timestamp=now_utc,
    )
    for c in changes[:20]:
        icon = _PLAN_LIGHT_ICON.get(c.new_light, "⚪")
        before = _PLAN_LIGHT_NAME.get(c.old_light or "", "mới")
        reasons = "\n".join(f"• {r}" for r in c.reasons[:3])
        embed.add_field(
            name=f"{icon} {c.ticker} — {_PLAN_LIGHT_NAME.get(c.new_light, c.new_light)}"
            f" (trước: {before})",
            value=(f"{c.summary}\n{reasons}" if c.summary else reasons)[:1024] or "—",
            inline=False,
        )
    if len(changes) > 20:
        embed.set_footer(text=f"… và {len(changes) - 20} kế hoạch khác — xem dashboard.")
    return embed
