"""Embed builder for proactive watch alerts.

Owner: bot segment (formatting adapter).
No business logic — pure presentation.

Used by: bot.ProactiveWatchSubscriber
"""

from __future__ import annotations

import datetime

import discord

from src.platform.events import ProactiveWatchAlertFiredEvent

# Condition type → human-readable label
_CONDITION_LABELS: dict[str, str] = {
    "PRICE_ABOVE":     "Giá vượt ngưỡng trên",
    "PRICE_BELOW":     "Giá giảm dưới ngưỡng",
    "CHANGE_PCT_UP":   "Tăng %",
    "CHANGE_PCT_DOWN": "Giảm %",
    "VOLUME_SPIKE":    "Volume đột biến",
    "THESIS_TRIGGER":  "Thesis trigger",
}

# Priority → Discord embed colour
_PRIORITY_COLOURS: dict[str | None, int] = {
    "HIGH":   0xE74C3C,   # red
    "MEDIUM": 0xF39C12,   # amber
    "LOW":    0x3498DB,   # blue
    None:     0x2ECC71,   # green — standard alerts
}

# Phase → emoji label
_PHASE_LABELS: dict[str, str] = {
    "morning": "🌅 Mở cửa",
    "midday":  "☀️ Giữa phiên",
    "pre_atc": "🔔 Trước ATC",
}


def build_proactive_alert_embed(
    event: ProactiveWatchAlertFiredEvent,
    now_utc: datetime.datetime | None = None,
) -> discord.Embed:
    """Build a Discord embed for a single fired proactive watch alert."""
    now_utc = now_utc or datetime.datetime.now(tz=datetime.UTC)
    colour = _PRIORITY_COLOURS.get(event.priority, 0x2ECC71)
    condition_label = _CONDITION_LABELS.get(event.condition_type, event.condition_type)
    phase_label = _PHASE_LABELS.get(event.phase, event.phase)

    title = f"🚨 Alert: **{event.ticker}** — {condition_label}"
    embed = discord.Embed(title=title, colour=colour)

    embed.add_field(name="Điều kiện", value=condition_label, inline=True)
    embed.add_field(
        name="Ngưỡng",
        value=f"{event.threshold:,.2f}" if event.threshold else "—",
        inline=True,
    )
    if event.triggered_price is not None:
        embed.add_field(
            name="Giá kích hoạt",
            value=f"{event.triggered_price:,.2f}",
            inline=True,
        )

    if event.priority:
        priority_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}.get(event.priority, "⚪")
        embed.add_field(
            name="Ưu tiên",
            value=f"{priority_emoji} {event.priority}",
            inline=True,
        )

    if event.label:
        embed.add_field(name="Label", value=event.label, inline=False)

    if event.note:
        note_display = event.note[:500] + "…" if len(event.note) > 500 else event.note
        embed.add_field(name="Ghi chú", value=note_display, inline=False)

    ict_offset = datetime.timezone(datetime.timedelta(hours=7))
    ict_now = now_utc.astimezone(ict_offset)
    embed.set_footer(
        text=(
            f"{phase_label}  •  "
            f"{ict_now.strftime('%H:%M ICT %d/%m/%Y')}  •  "
            f"alert #{event.alert_id}"
        )
    )
    return embed


def build_proactive_batch_embed(
    events: list[ProactiveWatchAlertFiredEvent],
    now_utc: datetime.datetime | None = None,
) -> discord.Embed:
    """Build a summary embed when multiple alerts fire in the same scan phase."""
    now_utc = now_utc or datetime.datetime.now(tz=datetime.UTC)
    phase = events[0].phase if events else "morning"
    phase_label = _PHASE_LABELS.get(phase, phase)

    embed = discord.Embed(
        title=f"🚨 Proactive Alerts — {phase_label} ({len(events)} tín hiệu)",
        colour=0xE74C3C if any(e.priority == "HIGH" for e in events) else 0xF39C12,
    )

    for ev in events[:10]:
        condition_label = _CONDITION_LABELS.get(ev.condition_type, ev.condition_type)
        priority_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}.get(ev.priority or "", "🟢")
        price_str = (
            f" → {ev.triggered_price:,.0f}"
            if ev.triggered_price is not None
            else ""
        )
        value_parts = [f"{priority_emoji} {condition_label}{price_str}"]
        if ev.label:
            value_parts.append(f"_{ev.label}_")
        if ev.note:
            short_note = ev.note[:120] + "…" if len(ev.note) > 120 else ev.note
            value_parts.append(short_note)

        embed.add_field(
            name=f"**{ev.ticker}**",
            value="\n".join(value_parts),
            inline=False,
        )

    if len(events) > 10:
        embed.add_field(name="…", value=f"và {len(events) - 10} alert khác", inline=False)

    ict_offset = datetime.timezone(datetime.timedelta(hours=7))
    ict_now = now_utc.astimezone(ict_offset)
    embed.set_footer(text=f"{phase_label}  •  {ict_now.strftime('%H:%M ICT %d/%m/%Y')}")
    return embed
