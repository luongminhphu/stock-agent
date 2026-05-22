"""Embed builder for ThesisDebateAgent output.

Owner: bot segment.
Pure formatting — no business logic, no DB access.
"""

from __future__ import annotations

import discord

from src.ai.schemas.thesis_debate import ChallengeStrength, DebateOutput

# Strength → emoji mapping
_STRENGTH_ICON: dict[ChallengeStrength, str] = {
    ChallengeStrength.CRITICAL: "🔴",
    ChallengeStrength.SIGNIFICANT: "🟠",
    ChallengeStrength.MODERATE: "🟡",
    ChallengeStrength.MINOR: "🟢",
}

_STRENGTH_ORDER: dict[ChallengeStrength, int] = {
    ChallengeStrength.CRITICAL: 0,
    ChallengeStrength.SIGNIFICANT: 1,
    ChallengeStrength.MODERATE: 2,
    ChallengeStrength.MINOR: 3,
}


def build_debate_embed(
    thesis_id: int,
    ticker: str,
    result: DebateOutput,
) -> discord.Embed:
    """Build a Discord embed from a DebateOutput."""
    # Embed color: red if critical challenges exist, orange if significant, else teal
    strengths = {c.strength for c in result.challenges}
    if ChallengeStrength.CRITICAL in strengths:
        color = discord.Color.red()
    elif ChallengeStrength.SIGNIFICANT in strengths:
        color = discord.Color.orange()
    else:
        color = discord.Color.teal()

    embed = discord.Embed(
        title=f"🤺 Thesis Debate — {ticker} #{thesis_id}",
        description=(
            f"**Verdict:** {result.verdict}\n\n"
            f"**Overall stance:** `{result.overall_stance.value}`  "
            f"**Confidence:** `{result.confidence}%`"
        ),
        color=color,
    )

    # Sort challenges: critical → significant → moderate → minor
    sorted_challenges = sorted(
        result.challenges,
        key=lambda c: _STRENGTH_ORDER.get(c.strength, 99),
    )

    for i, challenge in enumerate(sorted_challenges[:8], start=1):
        icon = _STRENGTH_ICON.get(challenge.strength, "•")
        field_name = f"{icon} #{i} · {challenge.area} · `{challenge.strength.value}`"
        counter = (
            f"\n> 💡 _{challenge.counter_argument[:100]}_"
            if challenge.counter_argument
            else ""
        )
        field_value = f"{challenge.challenge[:200]}{counter}"
        embed.add_field(name=field_name, value=field_value, inline=False)

    if len(result.challenges) > 8:
        embed.add_field(
            name="…",
            value=f"and {len(result.challenges) - 8} more challenge(s).",
            inline=False,
        )

    # Action block
    if result.suggested_action:
        embed.add_field(
            name="📌 Suggested action",
            value=result.suggested_action,
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Thesis #{thesis_id} · "
            "Use /review_thesis to generate AI recommendations · "
            "/thesis list to see all theses"
        )
    )
    return embed
