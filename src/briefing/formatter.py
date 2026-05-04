"""Briefing formatter — converts BriefOutput to human-readable strings.

Owner: briefing segment.
Callers: bot adapters, API response builders.

This module knows Discord markdown but NOT Discord SDK.
It returns plain strings; bot/api layers decide how to send them.
"""

from __future__ import annotations

import re
from collections import defaultdict

from src.ai.schemas import ActionPriority, BriefOutput, MarketSentiment, PrioritizedAction

_SENTIMENT_EMOJI = {
    MarketSentiment.RISK_ON: "🟢",
    MarketSentiment.RISK_OFF: "🔴",
    MarketSentiment.MIXED: "🟡",
    MarketSentiment.UNCERTAIN: "⚪",
}

_SENTIMENT_LABEL = {
    MarketSentiment.RISK_ON: "Risk-On",
    MarketSentiment.RISK_OFF: "Risk-Off",
    MarketSentiment.MIXED: "Mixed",
    MarketSentiment.UNCERTAIN: "Uncertain",
}

# Bucket config: (emoji, header label, show_reason, bold_ticker)
_PRIORITY_CONFIG: dict[ActionPriority, tuple[str, str, bool, bool]] = {
    ActionPriority.ACT_TODAY:  ("🔴", "Hành động hôm nay",  True,  True),
    ActionPriority.WATCH_MORE: ("🟡", "Theo dõi thêm",       True,  False),
    ActionPriority.SKIP_TODAY: ("⚪",  "Bỏ qua hôm nay",     False, False),
}

# Discord Embed description hard limit; we stay 96 chars below to leave
# room for the dropped-sections notice appended at the end.
_DISCORD_CHAR_LIMIT = 4096
_DEFAULT_CHAR_LIMIT = _DISCORD_CHAR_LIMIT - 96


def _inline(text: str) -> str:
    """Collapse stray single newlines to a space so tickers stay inline.

    Double newlines (paragraph breaks) are preserved intentionally.
    """
    return re.sub(r"(?<!\n)\n(?!\n)", " ", text).strip()


def _format_prioritized_actions(actions: list[PrioritizedAction]) -> list[str]:
    """Render prioritized_actions into Discord markdown lines.

    Layout per bucket:

      🔴 **Hành động hôm nay**
      • **VCB** Review stop-loss trước 9h  — _Giá tiếp cận stop 82,000_ [conf: 0.6]

      🟡 **Theo dõi thêm**
      • VNM Chờ xác nhận volume phiên tiếp theo  — _Volume chưa đủ_

      ⚪ _Bỏ qua: HPG, MSN_

    SKIP_TODAY is collapsed to a single comma-joined line to save vertical space.
    ACT_TODAY and WATCH_MORE render one bullet per action.
    """
    if not actions:
        return []

    buckets: dict[ActionPriority, list[PrioritizedAction]] = defaultdict(list)
    for a in actions:
        buckets[a.priority].append(a)

    lines: list[str] = []

    # Render in fixed order: ACT_TODAY → WATCH_MORE → SKIP_TODAY
    for priority in (ActionPriority.ACT_TODAY, ActionPriority.WATCH_MORE, ActionPriority.SKIP_TODAY):
        items = buckets.get(priority)
        if not items:
            continue

        emoji, header, show_reason, bold_ticker = _PRIORITY_CONFIG[priority]

        if priority == ActionPriority.SKIP_TODAY:
            # Collapse to single line: ⚪ _Bỏ qua: HPG, MSN, VHM_
            tickers_or_actions = ", ".join(
                (a.ticker or a.action[:20]) for a in items
            )
            lines += ["", f"{emoji} _Bỏ qua hôm nay: {tickers_or_actions}_"]
            continue

        lines += ["", f"{emoji} **{header}**"]
        for a in items:
            ticker_part = f"**{a.ticker}** " if bold_ticker and a.ticker else (f"{a.ticker} " if a.ticker else "")
            action_text = _inline(a.action)
            line = f"\u2022 {ticker_part}{action_text}"

            if show_reason and a.reason:
                line += f"  \u2014 _{_inline(a.reason)}_"

            # Flag low confidence explicitly so investor knows to double-check
            if a.confidence < 0.7:
                line += f" `conf:{a.confidence:.0%}`"

            lines.append(line)

    return lines


def _build_sections(brief: BriefOutput, brief_type: str) -> list[list[str]]:
    """Return brief content as an ordered list of line-groups (sections).

    Each section is a list[str] that should be appended together.
    Sections are ordered by investor priority:
        1. Header (always included — never dropped)
        2. Watchlist alerts
        3. Prioritized actions / action items
        4. Ticker summaries
        5. Portfolio summary
    """
    emoji = _SENTIMENT_EMOJI.get(brief.sentiment, "⚪")
    label = _SENTIMENT_LABEL.get(brief.sentiment, str(brief.sentiment))

    header: list[str] = [
        f"**📈 {brief_type.title()}** \u2014 {emoji} `{label}`",
        "",
        f"**{brief.headline}**",
        "",
        _inline(brief.summary),
    ]

    if brief.key_movers:
        movers_inline = "  \u2022  ".join(f"**{m}**" for m in brief.key_movers)
        header += ["", f"🔥 {movers_inline}"]

    sections: list[list[str]] = [header]

    if brief.watchlist_alerts:
        block: list[str] = ["", "**👁️ Watchlist**"]
        for alert in brief.watchlist_alerts:
            block.append(f"\u2022 {_inline(alert)}")
        sections.append(block)

    if brief.prioritized_actions:
        action_lines = _format_prioritized_actions(brief.prioritized_actions)
        if action_lines:
            sections.append(action_lines)
    elif brief.action_items:
        block = ["", "**✅ Actions**"]
        for item in brief.action_items:
            block.append(f"\u2022 {_inline(item)}")
        sections.append(block)

    if brief.ticker_summaries:
        block = ["", "**📊 Ticker**"]
        for ts in brief.ticker_summaries:
            signal_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(ts.signal, "⚪")
            pct = f"+{ts.change_pct:.1f}%" if ts.change_pct >= 0 else f"{ts.change_pct:.1f}%"
            block.append(
                f"{signal_emoji} **{ts.ticker}** `{ts.price:,.0f}` ({pct}) \u2014 {ts.one_line}"
            )
            if ts.watch_reason:
                block.append(f"  \u21b3 _{ts.watch_reason}_")
        sections.append(block)

    if brief.portfolio_summary:
        block = ["", "**💼 Portfolio**"]
        for item in brief.portfolio_summary:
            block.append(f"\u2022 {_inline(item)}")
        sections.append(block)

    return sections


def format_brief(brief: BriefOutput, brief_type: str = "brief", char_limit: int = _DEFAULT_CHAR_LIMIT) -> str:
    """Format a BriefOutput as a Discord-ready markdown string.

    Assembles sections in priority order and stops adding sections once
    char_limit would be exceeded. Appends a notice when sections are
    dropped so the investor knows the brief was clipped.

    Prefers prioritized_actions over deprecated action_items.
    Falls back to action_items if prioritized_actions is empty
    (backward compat with old BriefSnapshot records).

    Args:
        brief:      Structured output from BriefingAgent.
        brief_type: Label shown in the header (e.g. "Morning Brief", "EOD Brief").
        char_limit: Max characters before a dropped-sections notice is appended.
                    Defaults to 4000 to leave headroom within Discord's 4096 limit.

    Returns:
        Multi-line string with Discord markdown formatting, within char_limit.
    """
    sections = _build_sections(brief, brief_type)

    assembled: list[str] = []
    dropped = 0

    for i, section in enumerate(sections):
        candidate = "\n".join(assembled + section)
        if i == 0:
            # Header is always included — it is always shorter than char_limit
            assembled += section
        elif len(candidate) <= char_limit:
            assembled += section
        else:
            dropped += 1

    if dropped:
        notice = f"\n_{dropped} section(s) không hiển thị do giới hạn Discord (4096 ký tự)._"
        assembled.append(notice)

    return "\n".join(assembled)


def format_morning_brief(brief: BriefOutput) -> str:
    """Convenience wrapper for morning brief formatting."""
    return format_brief(brief, brief_type="Morning Brief")


def format_eod_brief(brief: BriefOutput) -> str:
    """Convenience wrapper for EOD brief formatting."""
    return format_brief(brief, brief_type="EOD Brief")
