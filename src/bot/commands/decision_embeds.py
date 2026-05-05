"""Decision embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (DecisionReplayScheduler) and
bot/commands/decision.py.
"""

from __future__ import annotations

import datetime

import discord

_VERDICT_ICON: dict[str, str] = {
    "CORRECT": "\u2705",    # ✅
    "INCORRECT": "\u274c",  # ❌
    "MIXED": "\U0001f7e1",  # 🟡
}


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
        color=0x4F98A3,
    )
    embed.set_footer(text=f"{len(results)} quy\u1ebft \u0111\u1ecbnh \u0111\u01b0\u1ee3c \u0111\u00e1nh gi\u00e1 l\u00fac {ict_time}")
    return embed
