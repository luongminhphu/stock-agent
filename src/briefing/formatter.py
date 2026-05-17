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
    MarketSentiment.RISK_ON:   "🟢",
    MarketSentiment.RISK_OFF:  "🔴",
    MarketSentiment.MIXED:     "🟡",
    MarketSentiment.UNCERTAIN: "❓",
    # Legacy fallbacks
    MarketSentiment.BULLISH:   "🟢",
    MarketSentiment.BEARISH:   "🔴",
    MarketSentiment.NEUTRAL:   "⚪",
}

_SENTIMENT_LABEL = {
    MarketSentiment.RISK_ON:   "Risk-On",
    MarketSentiment.RISK_OFF:  "Risk-Off",
    MarketSentiment.MIXED:     "Mixed",
    MarketSentiment.UNCERTAIN: "Uncertain",
    # Legacy fallbacks
    MarketSentiment.BULLISH:   "Bullish",
    MarketSentiment.BEARISH:   "Bearish",
    MarketSentiment.NEUTRAL:   "Neutral",
}

# Bucket config: (emoji, header label, show_reason, bold_ticker)
_PRIORITY_CONFIG: dict[ActionPriority, tuple[str, str, bool, bool]] = {
    ActionPriority.ACT_TODAY:  ("🔴", "Hành động hôm nay",  True,  True),
    ActionPriority.WATCH_MORE: ("🟡", "Theo dõi thêm",       True,  False),
    ActionPriority.SKIP_TODAY: ("⚪",  "Bỏ qua hôm nay",     False, False),
}

# Ticker signal emoji — corrected (was broken surrogate pairs)
_TICKER_SIGNAL_EMOJI: dict[str, str] = {
    "bullish": "🟢",
    "bearish": "🔴",
    "neutral": "⚪",
}

# Safe limit per Discord embed description (Discord max is 4096)
_DISCORD_PAGE_LIMIT = 4000
_DEFAULT_CHAR_LIMIT = _DISCORD_PAGE_LIMIT - 96


def _inline(text: str) -> str:
    return re.sub(r"(?<!\n)\n(?!\n)", " ", text).strip()


def _format_prioritized_actions(actions: list[PrioritizedAction]) -> list[str]:
    if not actions:
        return []

    buckets: dict[ActionPriority, list[PrioritizedAction]] = defaultdict(list)
    for a in actions:
        buckets[a.priority].append(a)

    lines: list[str] = []

    for priority in (ActionPriority.ACT_TODAY, ActionPriority.WATCH_MORE, ActionPriority.SKIP_TODAY):
        items = buckets.get(priority)
        if not items:
            continue

        emoji, header, show_reason, bold_ticker = _PRIORITY_CONFIG[priority]

        if priority == ActionPriority.SKIP_TODAY:
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

            reason = getattr(a, "reason", None) or getattr(a, "rationale", None)
            if show_reason and reason:
                line += f"  \u2014 _{_inline(reason)}_"

            if a.confidence < 0.7:
                line += f" `conf:{a.confidence:.0%}`"

            lines.append(line)

    return lines


def _build_sections(brief: BriefOutput, brief_type: str) -> list[list[str]]:
    emoji = _SENTIMENT_EMOJI.get(brief.sentiment, "⚪")
    label = _SENTIMENT_LABEL.get(brief.sentiment, str(brief.sentiment))

    header: list[str] = [
        f"**\ud83d\udcc8 {brief_type.title()}** \u2014 {emoji} `{label}`",
        "",
        f"**{brief.headline}**",
        "",
        _inline(brief.summary),
    ]

    if brief.key_movers:
        movers_inline = "  \u2022  ".join(f"**{m}**" for m in brief.key_movers)
        header += ["", f"\ud83d\udd25 {movers_inline}"]

    sections: list[list[str]] = [header]

    if brief.watchlist_alerts:
        block: list[str] = ["", "**\ud83d\udc41\ufe0f Watchlist**"]
        for alert in brief.watchlist_alerts:
            block.append(f"\u2022 {_inline(alert)}")
        sections.append(block)

    if brief.prioritized_actions:
        action_lines = _format_prioritized_actions(brief.prioritized_actions)
        if action_lines:
            sections.append(action_lines)
    elif brief.action_items:
        block = ["", "**\u2705 Actions**"]
        for item in brief.action_items:
            block.append(f"\u2022 {_inline(item)}")
        sections.append(block)

    if brief.ticker_summaries:
        block = ["", "**\ud83d\udcca Ticker**"]
        for ts in brief.ticker_summaries:
            signal_emoji = _TICKER_SIGNAL_EMOJI.get(
                getattr(ts, "signal", "neutral"), "⚪"
            )
            price = getattr(ts, "price", 0.0)
            change_pct = getattr(ts, "change_pct", 0.0)
            one_line = getattr(ts, "one_line", "") or getattr(ts, "one_liner", "")
            watch_reason = getattr(ts, "watch_reason", "")
            pct = f"+{change_pct:.1f}%" if change_pct >= 0 else f"{change_pct:.1f}%"
            block.append(
                f"{signal_emoji} **{ts.ticker}** `{price:,.0f}` ({pct}) \u2014 {one_line}"
            )
            if watch_reason:
                block.append(f"  \u21b3 _{watch_reason}_")
        sections.append(block)

    if brief.portfolio_summary:
        block = ["", "**\ud83d\udcbc Portfolio**"]
        for item in brief.portfolio_summary:
            block.append(f"\u2022 {_inline(item)}")
        sections.append(block)

    return sections


def build_brief_pages(
    brief: BriefOutput,
    brief_type: str = "brief",
    page_limit: int = _DISCORD_PAGE_LIMIT,
) -> list[str]:
    """Pack brief sections into Discord-safe pages (each ≤ page_limit chars).

    Sections are never dropped — if a section overflows the current page it
    starts a new one.  Each section that is itself longer than page_limit is
    hard-split at page_limit as a last resort.

    Returns a list of at least one non-empty string.
    """
    sections = _build_sections(brief, brief_type)
    pages: list[str] = []
    current_lines: list[str] = []

    for section in sections:
        section_text = "\n".join(section)

        # Section alone exceeds limit — hard-split as last resort
        if len(section_text) > page_limit:
            # Flush current page first
            if current_lines:
                pages.append("\n".join(current_lines))
                current_lines = []
            # Chunk the oversized section
            while section_text:
                pages.append(section_text[:page_limit])
                section_text = section_text[page_limit:]
            continue

        candidate = "\n".join(current_lines + section)
        if current_lines and len(candidate) > page_limit:
            # Flush and start fresh page with this section
            pages.append("\n".join(current_lines))
            current_lines = list(section)
        else:
            current_lines += section

    if current_lines:
        pages.append("\n".join(current_lines))

    return pages or [""]


def format_brief(
    brief: BriefOutput,
    brief_type: str = "brief",
    char_limit: int = _DEFAULT_CHAR_LIMIT,
) -> str:
    """Single-string formatter — kept for backward compat (API/dashboard callers).

    Sections that fit within char_limit are included; excess sections are noted.
    For multi-embed Discord output use build_brief_pages() instead.
    """
    sections = _build_sections(brief, brief_type)

    assembled: list[str] = []
    dropped = 0

    for i, section in enumerate(sections):
        candidate = "\n".join(assembled + section)
        if i == 0:
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
    return format_brief(brief, brief_type="Morning Brief")


def format_eod_brief(brief: BriefOutput) -> str:
    return format_brief(brief, brief_type="EOD Brief")
