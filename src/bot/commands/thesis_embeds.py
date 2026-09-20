"""Thesis embed builders and display constants.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by thesis_crud.py, thesis_review.py, and scheduler.py.
"""

from __future__ import annotations

import datetime
import json
import logging

import discord

from src.bot.discord_helper import (
    COLORS,
    STATUS_ICONS,
    VERDICT_ICONS,
    confidence_bar,
    fmt_ict,
    truncate,
)
from src.thesis.models import ReviewVerdict, ThesisStatus

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

# Drift verdict → icon (string keys from AI output)
_DRIFT_VERDICT_ICON: dict[str, str] = {
    "bullish": VERDICT_ICONS["BULLISH"],
    "bearish": VERDICT_ICONS["BEARISH"],
    "neutral": VERDICT_ICONS["NEUTRAL"],
}

# Conviction drift severity → icon
_CONVICTION_SEVERITY_ICON: dict[str, str] = {
    "CRITICAL": "\U0001f53b",  # 🔻
    "HIGH": "\u2b07\ufe0f",  # ⬇️
    "MEDIUM": "\U0001f4c9",  # 📉
}


def _parse_json_list(raw: str | list | None) -> list:
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


def _dominant_verdict_color(reviews: list) -> int:
    """Derive sidebar color from dominant verdict in a list of ThesisReview objects."""
    bullish = sum(1 for r in reviews if str(r.verdict).upper() == "BULLISH")
    bearish = sum(1 for r in reviews if str(r.verdict).upper() == "BEARISH")
    if bullish > bearish:
        return COLORS.GREEN
    if bearish > bullish:
        return COLORS.RED
    return COLORS.TEAL  # neutral/mixed → default info color


def _dominant_drift_color(reviewed_signals: list[tuple]) -> int:
    """Derive sidebar color from dominant drift verdict in (DriftSignal, ThesisReview) tuples."""
    bullish = sum(1 for _, r in reviewed_signals if r and str(r.verdict).upper() == "BULLISH")
    bearish = sum(1 for _, r in reviewed_signals if r and str(r.verdict).upper() == "BEARISH")
    if bullish > bearish:
        return COLORS.GREEN
    if bearish > bullish:
        return COLORS.RED
    return COLORS.ORANGE  # drift alert with no clear direction → orange


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
        verdict = ReviewVerdict(raw_verdict)
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
    reviews: list,
    now_utc: datetime.datetime,
    upcoming_catalysts: list[dict] | None = None,
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


def build_drift_embed(
    reviewed_signals: list[tuple],
    now_utc: datetime.datetime,
    conviction_signals: list | None = None,
    drift_threshold_pct: float | None = None,
) -> discord.Embed:
    """Build embed for ThesisDriftScheduler drift alert notification.

    Args:
        reviewed_signals:    List of (DriftSignal, ThesisReview | None) tuples.
        now_utc:             UTC datetime of the scan.
        conviction_signals:  Optional list of ConvictionDriftSignal objects.
        drift_threshold_pct: Threshold shown in footer. If None, loads from
                             settings as fallback (prefer injecting explicitly
                             to avoid hidden import cost in hot paths).
    """
    lines: list[str] = []

    for signal, review in reviewed_signals:
        if review is None:
            lines.append(
                f"\u26aa **{signal.ticker}** {signal.direction}"
                f"{abs(signal.drift_pct):.1f}% drift \u2192 review unavailable"
            )
            continue
        icon = _DRIFT_VERDICT_ICON.get(str(review.verdict).lower(), "\u26aa")
        lines.append(
            f"{icon} **{signal.ticker}** {signal.direction}{abs(signal.drift_pct):.1f}% "
            f"drift \u2192 AI verdict: **{review.verdict}** "
            f"(confidence {review.confidence:.0%})"
        )

    if conviction_signals:
        if lines:
            lines.append("")
        lines.append("**\U0001f4ca Conviction Drift**")
        for sig in conviction_signals:
            sev_icon = _CONVICTION_SEVERITY_ICON.get(sig.severity, "\U0001f4c9")
            lines.append(
                f"{sev_icon} **{sig.ticker}** `{sig.pattern.value}` "
                f"{sig.reference_score:.2f}\u2192{sig.current_score:.2f} "
                f"(-{sig.drop_pct:.1f}%) [{sig.severity}]"
            )

    # Resolve threshold for footer — inject > settings fallback
    threshold_pct = drift_threshold_pct
    if threshold_pct is None:
        try:
            from src.platform.config import settings  # noqa: PLC0415

            threshold_pct = settings.thesis_drift_threshold_pct
        except Exception:  # noqa: BLE001
            threshold_pct = 0.0

    embed = discord.Embed(
        title="\u26a1 Thesis Drift Alert",
        description="\n".join(lines) if lines else "Không có tín hiệu.",
        color=_dominant_drift_color(reviewed_signals) if reviewed_signals else COLORS.ORANGE,
    )
    embed.set_footer(
        text=(
            f"Drift \u2265{threshold_pct:.0f}% detected lúc "
            f"{fmt_ict(now_utc, fmt='%H:%M ICT')} \u00b7 stock-agent"
        )
    )
    return embed


def build_stop_breach_embed(outcomes: list, now_utc: datetime.datetime) -> discord.Embed:
    """Wave 6c: embed cho stop-breach auto-invalidation outcomes.

    Mỗi outcome là StopBreachOutcome từ thesis.stop_breach_service.
    Nhóm: invalidated (đã xử lý) trước, observed/ai_not_confirmed/ai_failed sau.
    """
    invalidated = [o for o in outcomes if o.action == "invalidated"]
    near_stop = [o for o in outcomes if o.action == "near_stop"]
    others = [o for o in outcomes if o.action not in ("invalidated", "near_stop")]

    if invalidated:
        color, title = discord.Color.red(), "🛑 Stop-breach auto-invalidation"
    elif others:
        color, title = discord.Color.orange(), "🛑 Stop-breach detected"
    else:
        color, title = discord.Color.gold(), "⚠️ Giá tiến gần stop-loss"
    embed = discord.Embed(title=title, color=color, timestamp=now_utc)

    if invalidated:
        lines = []
        for o in invalidated:
            conf = f"{o.ai_confidence:.0%}" if o.ai_confidence is not None else "—"
            head = (
                f"**{o.ticker}** — giá {o.current_price:,.0f} xuyên stop {o.stop_loss:,.0f} "
                f"({o.overshoot_pct:.1f}%)"
            )
            # Wave 9.4: vị thế khóa hoàn toàn (sellable == 0) → exit_signal
            # vô nghĩa; đổi messaging thành "theo dõi mở khóa" thay vì
            # khuyến nghị thoát lặp lại mỗi ngày.
            locked_qty = getattr(o, "locked_qty", 0.0) or 0.0
            sellable = getattr(o, "sellable_qty", None)
            if locked_qty > 0 and sellable is not None and sellable <= 0:
                reason = getattr(o, "locked_reason", None) or "khóa bán"
                until = getattr(o, "locked_until", None)
                if isinstance(until, datetime.datetime):
                    until = until.date()
                until_txt = f", mở khóa dự kiến {until:%d/%m/%Y}" if until else ""
                lines.append(
                    f"{head}\n"
                    f"→ Thesis #{o.thesis_id} vô hiệu, nhưng vị thế bị khóa "
                    f"({locked_qty:,.0f} cp — {reason}{until_txt}). Không thể bán — "
                    f"đặt nhắc xem lại khi mở khóa."
                )
            else:
                lines.append(
                    f"{head}\n"
                    f"→ Thesis #{o.thesis_id} đã INVALIDATE (AI: {o.ai_verdict} {conf}, "
                    f"action: {o.ai_action or '—'})"
                )
        embed.add_field(name="Đã invalidate", value="\n\n".join(lines)[:1024], inline=False)

    if others:
        lines = []
        label = {
            "observed": "quan sát (auto-invalidate tắt)",
            "ai_not_confirmed": "AI chưa xác nhận",
            "ai_failed": "AI confirm thất bại — cần xem tay",
        }
        for o in others:
            extra = ""
            if o.action == "ai_not_confirmed" and o.ai_verdict:
                conf = f"{o.ai_confidence:.0%}" if o.ai_confidence is not None else "—"
                extra = f" (AI: {o.ai_verdict} {conf})"
            lines.append(
                f"**{o.ticker}** — giá {o.current_price:,.0f} xuyên stop {o.stop_loss:,.0f} "
                f"({o.overshoot_pct:.1f}%) → {label.get(o.action, o.action)}{extra} — "
                f"thesis #{o.thesis_id} cần xem xét"
            )
        embed.add_field(name="Cần xem xét thủ công", value="\n".join(lines)[:1024], inline=False)

    if near_stop:
        # Wave C3: cảnh báo sớm theo ATR — thesis chưa xuyên stop, không AI, không mutate.
        lines = []
        for o in near_stop:
            atr_txt = f"{o.stop_distance_atr:.2f} ATR" if o.stop_distance_atr is not None else "—"
            quality = getattr(o, "source_quality", "quote")
            stale = " · data cũ" if quality in ("stale", "fallback") else ""
            lines.append(
                f"**{o.ticker}** — giá {o.current_price:,.0f} còn cách stop {o.stop_loss:,.0f} "
                f"{atr_txt} ({abs(o.overshoot_pct):.1f}%){stale} → thesis #{o.thesis_id}: "
                f"kiểm tra lại giả định / cân nhắc giảm vị thế trước khi xuyên stop"
            )
        embed.add_field(
            name="Sắp chạm stop (< 1 ATR14)", value="\n".join(lines)[:1024], inline=False
        )

    embed.set_footer(text="StopBreachService · rule + AI confirmation · 15-min drift tick")
    return embed


# ---------------------------------------------------------------------------
# Wave D3: Watchdog embeds — render WatchdogRunResult (thesis.watchdog_service)
# ---------------------------------------------------------------------------

_WATCHDOG_ACTION_VN: dict[str, str] = {
    "HOLD": "Giữ",
    "REVIEW_SOON": "Xem lại sớm",
    "REVIEW_URGENT": "Xem lại ngay",
    "CONSIDER_EXIT": "Cân nhắc thoát",
}


def _watchdog_line(r) -> str:  # noqa: ANN001 — WatchdogTickerResult (duck-typed)
    """Một dòng cho một thesis: health → hành động → stop/dữ liệu → auto-invalidate."""
    if r.health_score is not None:
        head = f"**{r.ticker}** — {r.overall_health} ({r.health_score}/100)"
    else:
        head = f"**{r.ticker}** — rule-based" + (" (AI lỗi)" if r.agent_failed else "")
    parts = [head]
    if r.recommended_action:
        parts.append(_WATCHDOG_ACTION_VN.get(r.recommended_action, r.recommended_action))
    # near_stop là rule của thesis (NEAR_STOP_ATR) — bot chỉ đọc property.
    if getattr(r, "near_stop", False):
        parts.append(f"cách stop {r.stop_distance_atr:.1f} ATR")
    elif (
        r.stop_distance_atr is None
        and r.stop_loss_distance_pct is not None
        and r.stop_loss_distance_pct < 5.0
    ):
        parts.append(f"cách stop {r.stop_loss_distance_pct:.1f}%")
    if r.source_quality in ("stale", "fallback"):
        parts.append("dữ liệu cũ")
    line = " · ".join(parts)
    if r.auto_invalidated:
        line += f"\n→ Thesis #{r.thesis_id} đã tự vô hiệu (AI xác nhận)."
    return line


def build_watchdog_urgent_embed(run_result, now_utc: datetime.datetime) -> discord.Embed:  # noqa: ANN001
    """Embed cảnh báo khẩn — chỉ URGENT_ALERT, đẩy vào alert channel ngay khi có."""
    urgent = run_result.urgent_alerts
    embed = discord.Embed(
        title=f"🔴 Watchdog: {len(urgent)} thesis cần xem lại ngay",
        color=discord.Color.red(),
        timestamp=now_utc,
    )
    embed.description = truncate("\n\n".join(_watchdog_line(r) for r in urgent), 4096)
    embed.set_footer(text="WatchdogService · assumption health + TickerContext · 08:20 ICT")
    return embed


def build_watchdog_digest_embed(run_result, now_utc: datetime.datetime) -> discord.Embed:  # noqa: ANN001
    """Embed tổng hợp buổi sáng — SILENT_WARNING + đếm OK/lỗi. Không lặp lại URGENT."""
    warnings = run_result.silent_warnings
    healthy = run_result.healthy
    urgent = run_result.urgent_alerts
    embed = discord.Embed(
        title="🟡 Watchdog sáng — sức khỏe thesis",
        color=discord.Color.gold() if warnings else discord.Color.dark_grey(),
        timestamp=now_utc,
    )
    summary = f"{len(healthy)} ổn · {len(warnings)} cần chú ý · {len(urgent)} khẩn"
    if run_result.errors:
        summary += f" · {len(run_result.errors)} lỗi ({', '.join(sorted(run_result.errors))})"
    embed.description = summary
    if warnings:
        embed.add_field(
            name="Cần chú ý",
            value=truncate("\n".join(_watchdog_line(r) for r in warnings), 1024),
            inline=False,
        )
    if urgent:
        embed.add_field(
            name="Khẩn (đã gửi alert riêng)",
            value=truncate(", ".join(r.ticker for r in urgent), 1024),
            inline=False,
        )
    embed.set_footer(text="WatchdogService · assumption health + TickerContext · 08:20 ICT")
    return embed
