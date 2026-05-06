"""Decision embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (DecisionReplayScheduler) and
bot/commands/decision.py.
"""

from __future__ import annotations

import datetime

import discord

# ---------------------------------------------------------------------------
# Shared verdict metadata
# ---------------------------------------------------------------------------

VERDICT_META: dict[str, dict] = {
    "CORRECT":   {"emoji": "\u2705",       "color": discord.Color.green()},   # ✅
    "INCORRECT": {"emoji": "\u274c",       "color": discord.Color.red()},     # ❌
    "MIXED":     {"emoji": "\u2696\ufe0f", "color": discord.Color.orange()},  # ⚖️
}
DEFAULT_VERDICT_META: dict = {"emoji": "\U0001f4cb", "color": discord.Color.blue()}  # 📋

_VERDICT_ICON: dict[str, str] = {
    "CORRECT":   "\u2705",      # ✅
    "INCORRECT": "\u274c",      # ❌
    "MIXED":     "\U0001f7e1",  # 🟡
}

# ---------------------------------------------------------------------------
# Color standard (shared across all embed builders)
# ---------------------------------------------------------------------------

_COLOR_GREEN  = 0x57F287
_COLOR_RED    = 0xED4245
_COLOR_ORANGE = 0xFF6B35
_COLOR_TEAL   = 0x4F98A3


def _batch_outcome_color(results: list[dict]) -> int:
    """Derive sidebar color from majority outcome in a batch replay list."""
    correct   = sum(1 for i in results if str(getattr(i.get("decision"), "outcome_verdict", "")).upper() == "CORRECT")
    incorrect = sum(1 for i in results if str(getattr(i.get("decision"), "outcome_verdict", "")).upper() == "INCORRECT")
    if correct > incorrect:
        return _COLOR_GREEN
    if incorrect > correct:
        return _COLOR_RED
    return _COLOR_ORANGE


# ---------------------------------------------------------------------------
# Scheduler embed — batch end-of-day summary
# ---------------------------------------------------------------------------

def build_replay_embed(
    results: list[dict],
    now_utc: datetime.datetime,
) -> discord.Embed:
    """Build embed for DecisionReplayScheduler end-of-day summary.

    Args:
        results:  List of dicts with keys 'decision' (DecisionLog ORM) and
                  'replay' (ReplayOutput | None).
        now_utc:  Current UTC datetime for footer timestamp.

    Returns:
        discord.Embed ready to send.
    """
    lines: list[str] = []
    for item in results:
        d = item["decision"]
        r = item["replay"]
        icon = _VERDICT_ICON.get(str(d.outcome_verdict).upper(), "\u26aa")  # ⚪ fallback
        pnl_str = f"{d.outcome_pnl_pct:+.1f}%" if d.outcome_pnl_pct is not None else "N/A"
        line = f"{icon} **{d.ticker}** {d.decision_type} \u2192 {d.outcome_verdict} ({pnl_str})"
        if r and getattr(r, "key_lesson", None):
            line += f"\n    \U0001f4a1 _{r.key_lesson}_"  # 💡
        if r and getattr(r, "pattern_detected", None):
            line += f"\n    \U0001f50d Pattern: `{r.pattern_detected}`"  # 🔍
        lines.append(line)

    ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
    embed = discord.Embed(
        title="\U0001f504 Decision Replay \u2014 K\u1ebft qu\u1ea3 sau horizon",  # 🔄
        description="\n\n".join(lines),
        color=_batch_outcome_color(results),
    )
    embed.set_footer(
        text=f"{len(results)} quy\u1ebft \u0111\u1ecbnh \u0111\u01b0\u1ee3c \u0111\u00e1nh gi\u00e1 l\u00fac {ict_time}"
    )
    return embed


# ---------------------------------------------------------------------------
# Command embeds — /replay and /lessons
# ---------------------------------------------------------------------------

def build_single_replay_embed(decision_id: int, envelope) -> discord.Embed:
    """Build Discord embed for /replay command result.

    Args:
        decision_id: PK of the replayed decision (for title).
        envelope:    DecisionReplayEnvelope from DecisionService.replay_decision().

    Returns:
        discord.Embed ready to send as ephemeral followup.
    """
    verdict = envelope.outcome_verdict or "MIXED"
    meta = VERDICT_META.get(verdict, DEFAULT_VERDICT_META)
    replay = envelope.replay

    title = f"{meta['emoji']} Replay #{decision_id} \u2014 {envelope.ticker} [{verdict}]"

    if replay is None:
        return discord.Embed(
            title=title,
            description="ReplayAgent kh\u00f4ng kh\u1ea3 d\u1ee5ng. Outcome \u0111\u00e3 \u0111\u01b0\u1ee3c evaluate.",
            color=meta["color"],
        )

    embed = discord.Embed(title=title, color=meta["color"])

    what_right = getattr(replay, "what_went_right", None)
    what_wrong = getattr(replay, "what_went_wrong", None)
    key_lesson = getattr(replay, "key_lesson", None)
    pattern    = getattr(replay, "pattern_detected", None)
    adjustment = getattr(replay, "suggested_adjustment", None)
    confidence = getattr(replay, "confidence", None)

    if what_right:
        embed.add_field(name="\u2705 \u0110\u00fang \u1edf \u0111i\u1ec3m n\u00e0o", value=what_right, inline=False)
    if what_wrong:
        embed.add_field(name="\u274c Sai \u1edf \u0111i\u1ec3m n\u00e0o", value=what_wrong, inline=False)
    if key_lesson:
        embed.add_field(name="\U0001f4a1 Key lesson", value=key_lesson, inline=False)
    if pattern:
        embed.add_field(name="\U0001f501 Pattern", value=f"`{pattern}`", inline=True)
    if adjustment:
        embed.add_field(name="\U0001f3af \u0110i\u1ec1u ch\u1ec9nh g\u1ee3i \u00fd", value=adjustment, inline=False)

    if confidence is not None:
        conf_bar = "\u2588" * round(confidence * 10) + "\u2591" * (10 - round(confidence * 10))
        embed.set_footer(
            text=f"Confidence: {conf_bar} {confidence:.0%}  \u00b7  stock-agent replay"
        )

    return embed


def build_lessons_embed(
    rows: list,
    *,
    ticker: str | None,
    limit: int,
) -> discord.Embed:
    """Build Discord embed listing AI-generated lessons for /lessons command.

    Args:
        rows:   List of DecisionLog rows with key_lesson set (newest first).
        ticker: Optional ticker filter that was applied (for title).
        limit:  Requested limit (for footer display).

    Returns:
        discord.Embed ready to send as ephemeral followup.
    """
    title = f"\U0001f9e0 Lessons \u2014 {ticker.upper()}" if ticker else "\U0001f9e0 Lessons \u2014 T\u1ea5t c\u1ea3 m\u00e3"

    if not rows:
        scope = f"m\u00e3 **{ticker.upper()}**" if ticker else "b\u1ea5t k\u1ef3 m\u00e3 n\u00e0o"
        embed = discord.Embed(
            title=title,
            description=(
                f"Ch\u01b0a c\u00f3 b\u00e0i h\u1ecdc n\u00e0o \u0111\u01b0\u1ee3c ghi nh\u1eadn cho {scope}.\n"
                "D\u00f9ng `/replay <decision_id>` sau khi horizon qua \u0111\u1ec3 AI ph\u00e2n t\u00edch."
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
        text=f"Hi\u1ec3n th\u1ecb {len(rows)}/{limit} b\u00e0i h\u1ecdc m\u1edbi nh\u1ea5t  \u00b7  stock-agent"
    )
    return embed
