"""Pretrade command — /pretrade <ticker>
Owner: bot segment. Adapter only — no domain logic.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.ai.schemas import AlignmentStatus, ResolutionCategory, TradeDecision
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_pretrade_agent, get_quote_service
from src.platform.logging import get_logger

logger = get_logger(__name__)

_DECISION_META: dict[TradeDecision, dict] = {
    TradeDecision.BUY: {
        "emoji": "\u2705",
        "label": "GO",
        "color": discord.Color.green(),
    },
    TradeDecision.HOLD: {
        "emoji": "\u23f3",
        "label": "WAIT",
        "color": discord.Color.yellow(),
    },
    TradeDecision.SELL: {
        "emoji": "\u274c",
        "label": "AVOID",
        "color": discord.Color.red(),
    },
    TradeDecision.REDUCE: {
        "emoji": "\u26a0\ufe0f",
        "label": "REDUCE",
        "color": discord.Color.orange(),
    },
}

_ALIGNMENT_ICON: dict[AlignmentStatus, str] = {
    AlignmentStatus.ALIGNED: "\u2705",
    AlignmentStatus.NEUTRAL: "\u27a1\ufe0f",
    AlignmentStatus.MISALIGNED: "\u26a0\ufe0f",
}

_CATEGORY_ICON: dict[ResolutionCategory, str] = {
    ResolutionCategory.THESIS_CONFLICT:    "\U0001f4cb",
    ResolutionCategory.RISK_LIMIT:         "\U0001f6a8",
    ResolutionCategory.TIMING:             "\U0001f4b0",
    ResolutionCategory.MARKET_CONDITION:   "\U0001f4ca",
    ResolutionCategory.PORTFOLIO_BALANCE:  "\U0001f30d",
}

_PRIORITY_BADGE = {
    "BLOCKING": "[P1]",
    "HIGH":     "[P2]",
    "MEDIUM":   "[P3]",
    "LOW":      "[P4]",
}


class PretradeCog(BaseCog):
    @app_commands.command(
        name="pretrade",
        description="Cross-check thesis, signal, brief tr\u01b0\u1edbc khi v\u00e0o l\u1ec7nh",
    )
    @app_commands.describe(ticker="M\u00e3 c\u1ed5 phi\u1ebfu, VD: VCB")
    async def pretrade(self, interaction: discord.Interaction, ticker: str) -> None:
        await interaction.response.defer(ephemeral=False)

        user_id = str(interaction.user.id)
        try:
            from src.platform.db import get_session
            from src.thesis.pretrade_service import PreTradeService

            async with get_session() as session:
                svc = PreTradeService(
                    session=session,
                    quote_service=get_quote_service(),
                    pretrade_agent=get_pretrade_agent(),
                )
                result = await svc.check(ticker=ticker, user_id=user_id)
        except Exception as exc:
            logger.error("pretrade.command.error", ticker=ticker, error=str(exc))
            await self.send_error(
                interaction,
                title="Pre-trade check th\u1ea5t b\u1ea1i",
                description=f"Kh\u00f4ng th\u1ec3 ph\u00e2n t\u00edch `{ticker.upper()}`.\n`{exc}`",
            )
            return

        embed = _build_pretrade_embed(result)
        await interaction.followup.send(embed=embed, ephemeral=False)


def _build_pretrade_embed(result) -> discord.Embed:
    """Build Discord embed from PreTradeCheckOutput (current schema)."""
    # intended_action: TradeDecision (BUY/SELL/REDUCE/HOLD)
    meta = _DECISION_META.get(result.intended_action, _DECISION_META[TradeDecision.HOLD])
    conf_bar = "\u2588" * round(result.confidence * 10) + "\u2591" * (10 - round(result.confidence * 10))

    # verdict (BULLISH/BEARISH/…) shown in title alongside intended_action label
    verdict_str = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)

    embed = discord.Embed(
        title=f"{meta['emoji']} Pre-trade {result.ticker}: {meta['label']} ({verdict_str})",
        description=result.risk_summary,
        color=meta["color"],
    )

    # Overall alignment
    align_icon = _ALIGNMENT_ICON.get(result.alignment, "\u2753")
    embed.add_field(
        name="\ud83d\udcca \u0110\u1ed3ng thu\u1eadn",
        value=f"{align_icon} **Thesis alignment**: {result.alignment.value}",
        inline=False,
    )

    # Blocking issues
    if result.blocking_issues:
        embed.add_field(
            name="\u26a0\ufe0f V\u1ea5n \u0111\u1ec1 c\u1ea7n x\u1eed l\u00fd",
            value="\n".join(f"\u2022 {c}" for c in result.blocking_issues),
            inline=False,
        )

    # Resolution steps — only when not BUY and steps exist
    resolution_steps = result.resolution_steps or []
    if resolution_steps and result.intended_action != TradeDecision.BUY:
        steps = sorted(
            resolution_steps,
            key=lambda s: list(_PRIORITY_BADGE.keys()).index(s.priority)
            if s.priority in _PRIORITY_BADGE else 99,
        )
        lines: list[str] = []
        for step in steps:
            cat_icon = _CATEGORY_ICON.get(step.category, "\u2022")
            badge = _PRIORITY_BADGE.get(step.priority, "[P?]")
            lines.append(
                f"`{badge}` {cat_icon} **{step.issue}**"
                f"\n\u00a0\u00a0\u00a0\u00a0\u21b3 {step.resolution}"
            )
        embed.add_field(
            name="\U0001f5fa\ufe0f L\u1ed9 tr\u00ecnh \u2192 GO",
            value="\n".join(lines),
            inline=False,
        )

    # Thesis alignment note
    if result.thesis_alignment_note:
        embed.add_field(
            name="\U0001f4cb Thesis",
            value=result.thesis_alignment_note[:1000],
            inline=False,
        )

    # Sizing note
    if result.sizing_note:
        embed.add_field(
            name="\U0001f4b0 Sizing",
            value=result.sizing_note[:500],
            inline=False,
        )

    embed.set_footer(
        text=f"\u0110\u1ed9 tin c\u1eady: {conf_bar} {result.confidence:.0%}  \u00b7  stock-agent pre-trade"
    )
    return embed
