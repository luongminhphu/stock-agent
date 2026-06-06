"""Decision embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (DecisionReplayScheduler) and
bot/commands/decision.py.
"""

from __future__ import annotations

import datetime

import discord

from src.bot.discord_helper import COLORS, VERDICT_ICONS, confidence_bar, fmt_ict

# ---------------------------------------------------------------------------
# Shared verdict metadata
# ---------------------------------------------------------------------------

VERDICT_META: dict[str, dict] = {
    "CORRECT":   {"emoji": VERDICT_ICONS["CORRECT"],   "color": discord.Color.green()},
    "INCORRECT": {"emoji": VERDICT_ICONS["INCORRECT"], "color": discord.Color.red()},
    "MIXED":     {"emoji": VERDICT_ICONS["MIXED"],     "color": discord.Color.orange()},
}
DEFAULT_VERDICT_META: dict = {"emoji": "\U0001f4cb", "color": discord.Color.blue()}  # 📋

_VERDICT_ICON: dict[str, str] = {
    "CORRECT":   VERDICT_ICONS["CORRECT"],
    "INCORRECT": VERDICT_ICONS["INCORRECT"],
    "MIXED":     VERDICT_ICONS["MIXED"],
}


def _batch_outcome_color(results: list[dict]) -> int:
    """Derive sidebar color from majority outcome in a batch replay list."""
    correct   = sum(1 for i in results if str(getattr(i.get("decision"), "outcome_verdict", "")).upper() == "CORRECT")
    incorrect = sum(1 for i in results if str(getattr(i.get("decision"), "outcome_verdict", "")).upper() == "INCORRECT")
    if correct > incorrect:
        return COLORS.GREEN
    if incorrect > correct:
        return COLORS.RED
    return COLORS.ORANGE


# ---------------------------------------------------------------------------
# Scheduler embed — batch end-of-day summary
# ---------------------------------------------------------------------------

def build_replay_embed(
    results: list[dict],
    now_utc: datetime.datetime,
) -> discord.Embed:
    """Build embed for DecisionReplayScheduler end-of-day summary."""
    lines: list[str] = []
    for item in results:
        d = item["decision"]
        r = item["replay"]
        icon = _VERDICT_ICON.get(str(d.outcome_verdict).upper(), "\u26aa")
        pnl_str = f"{d.outcome_pnl_pct:+.1f}%" if d.outcome_pnl_pct is not None else "N/A"
        line = f"{icon} **{d.ticker}** {d.decision_type} \u2192 {d.outcome_verdict} ({pnl_str})"
        if r and getattr(r, "key_lesson", None):
            line += f"\n    \U0001f4a1 _{r.key_lesson}_"
        if r and getattr(r, "pattern_detected", None):
            line += f"\n    \U0001f50d Pattern: `{r.pattern_detected}`"
        lines.append(line)

    embed = discord.Embed(
        title="\U0001f504 Decision Replay \u2014 Kết quả sau horizon",
        description="\n\n".join(lines),
        color=_batch_outcome_color(results),
    )
    embed.set_footer(
        text=f"{len(results)} quyết định được đánh giá lúc {fmt_ict(now_utc, fmt='%H:%M ICT')}"
    )
    return embed


# ---------------------------------------------------------------------------
# Command embeds — /replay and /lessons
# ---------------------------------------------------------------------------

def build_single_replay_embed(decision_id: int, envelope) -> discord.Embed:
    """Build Discord embed for /replay command result."""
    verdict = envelope.outcome_verdict or "MIXED"
    meta = VERDICT_META.get(verdict, DEFAULT_VERDICT_META)
    replay = envelope.replay

    title = f"{meta['emoji']} Replay #{decision_id} \u2014 {envelope.ticker} [{verdict}]"

    if replay is None:
        return discord.Embed(
            title=title,
            description="ReplayAgent không khả dụng. Outcome đã được evaluate.",
            color=meta["color"],
        )

    embed = discord.Embed(title=title, color=meta["color"])

    _what_right_raw = getattr(replay, "what_went_right", None)
    _what_wrong_raw = getattr(replay, "what_went_wrong", None)
    # ReplayOutput returns list[str]; join for Discord embed value (max 1024 chars)
    what_right = "\n".join(f"• {x}" for x in _what_right_raw) if isinstance(_what_right_raw, list) else _what_right_raw
    what_wrong = "\n".join(f"• {x}" for x in _what_wrong_raw) if isinstance(_what_wrong_raw, list) else _what_wrong_raw
    key_lesson = getattr(replay, "key_lesson", None)
    pattern    = getattr(replay, "pattern_detected", None)
    adjustment = getattr(replay, "suggested_adjustment", None)
    conf       = getattr(replay, "confidence", None)

    if what_right:
        embed.add_field(name="\u2705 Đúng ở điểm nào", value=what_right[:1024], inline=False)
    if what_wrong:
        embed.add_field(name="\u274c Sai ở điểm nào", value=what_wrong[:1024], inline=False)
    if key_lesson:
        embed.add_field(name="\U0001f4a1 Key lesson", value=key_lesson, inline=False)
    if pattern:
        embed.add_field(name="\U0001f501 Pattern", value=f"`{pattern}`", inline=True)
    if adjustment:
        embed.add_field(name="\U0001f3af Điều chỉnh gợi ý", value=adjustment, inline=False)

    if conf is not None:
        embed.set_footer(
            text=f"Confidence: {confidence_bar(conf)} {conf:.0%}  \u00b7  stock-agent replay"
        )

    return embed


def build_lessons_embed(
    rows: list,
    *,
    ticker: str | None,
    limit: int,
) -> discord.Embed:
    """Build Discord embed listing AI-generated lessons for /lessons command."""
    title = f"\U0001f9e0 Lessons \u2014 {ticker.upper()}" if ticker else "\U0001f9e0 Lessons \u2014 Tất cả mã"

    if not rows:
        scope = f"mã **{ticker.upper()}**" if ticker else "bất kỳ mã nào"
        embed = discord.Embed(
            title=title,
            description=(
                f"Chưa có bài học nào được ghi nhận cho {scope}.\n"
                "Dùng `/replay <decision_id>` sau khi horizon qua để AI phân tích."
            ),
            color=discord.Color.greyple(),
        )
        embed.set_footer(text="stock-agent lessons")
        return embed

    embed = discord.Embed(title=title, color=discord.Color.blue())

    for row in rows:
        verdict = row.outcome_verdict or "?"
        meta = VERDICT_META.get(verdict, DEFAULT_VERDICT_META)
        date_str = row.decision_at.strftime("%d/%m/%Y") if row.decision_at else "N/A"
        pattern_str = f"  `{row.pattern_detected}`" if row.pattern_detected else ""
        field_name = (
            f"{meta['emoji']} #{row.id} \u00b7 {row.ticker} \u00b7 "
            f"{row.decision_type} \u00b7 {date_str}{pattern_str}"
        )
        embed.add_field(name=field_name, value=row.key_lesson, inline=False)

    embed.set_footer(
        text=f"Hiển thị {len(rows)}/{limit} bài học mới nhất  \u00b7  stock-agent"
    )
    return embed
