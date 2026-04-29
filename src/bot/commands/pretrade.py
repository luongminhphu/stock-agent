"""Pretrade command — /pretrade <ticker>
Owner: bot segment. Adapter only — no domain logic.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.ai.schemas import AlignmentStatus, TradeDecision
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_pretrade_agent, get_quote_service
from src.platform.logging import get_logger

logger = get_logger(__name__)

_DECISION_META: dict[TradeDecision, dict] = {
    TradeDecision.GO: {
        "emoji": "\u2705",
        "label": "GO",
        "color": discord.Color.green(),
    },
    TradeDecision.WAIT: {
        "emoji": "\u23f3",
        "label": "WAIT",
        "color": discord.Color.yellow(),
    },
    TradeDecision.AVOID: {
        "emoji": "\u274c",
        "label": "AVOID",
        "color": discord.Color.red(),
    },
}

_ALIGNMENT_ICON: dict[AlignmentStatus, str] = {
    AlignmentStatus.SUPPORT: "\u2705",
    AlignmentStatus.NEUTRAL: "\u27a1\ufe0f",
    AlignmentStatus.CONFLICT: "\u26a0\ufe0f",
    AlignmentStatus.NO_DATA: "\u2753",
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
    meta = _DECISION_META.get(result.decision, _DECISION_META[TradeDecision.WAIT])
    conf_bar = "\u2588" * round(result.confidence * 10) + "\u2591" * (10 - round(result.confidence * 10))

    embed = discord.Embed(
        title=f"{meta['emoji']} Pre-trade {result.ticker}: {meta['label']}",
        description=result.reasoning,
        color=meta["color"],
    )

    # Alignment matrix
    t_icon = _ALIGNMENT_ICON.get(result.thesis_alignment, "\u2753")
    s_icon = _ALIGNMENT_ICON.get(result.signal_alignment, "\u2753")
    b_icon = _ALIGNMENT_ICON.get(result.brief_alignment, "\u2753")
    embed.add_field(
        name="\ud83d\udcca \u0110\u1ed3ng thu\u1eadn ngu\u1ed3n",
        value=(
            f"{t_icon} **Thesis**: {result.thesis_alignment.value}\n"
            f"{s_icon} **Scan signal**: {result.signal_alignment.value}\n"
            f"{b_icon} **Brief h\u00f4m nay**: {result.brief_alignment.value}"
        ),
        inline=False,
    )

    if result.conflicts:
        embed.add_field(
            name="\u26a0\ufe0f Xung \u0111\u1ed9t",
            value="\n".join(f"\u2022 {c}" for c in result.conflicts),
            inline=False,
        )

    if result.conditions:
        embed.add_field(
            name="\u23f3 \u0110i\u1ec1u ki\u1ec7n c\u1ea7n th\u1ecfa (WAIT)",
            value="\n".join(f"\u2022 {c}" for c in result.conditions),
            inline=False,
        )

    if result.risk_flags:
        embed.add_field(
            name="\ud83d\udea8 R\u1ee7i ro theo d\u00f5i",
            value="\n".join(f"\u2022 {r}" for r in result.risk_flags),
            inline=False,
        )

    embed.set_footer(
        text=f"\u0110\u1ed9 tin c\u1eady: {conf_bar} {result.confidence:.0%}  \u00b7  stock-agent pre-trade"
    )
    return embed
