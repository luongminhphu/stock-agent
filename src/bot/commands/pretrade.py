"""Pretrade command — /pretrade <ticker> [action] [quantity]
Owner: bot segment. Adapter only — no domain logic.

P1: nhà đầu tư khai báo lệnh sắp làm (Mua / Bán / Giảm / Giữ); nhãn GO / KHÔNG
lấy từ proceed_recommendation của AI, không suy từ intended_action nữa.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from src.ai.schemas import AlignmentStatus, ResolutionCategory, TradeDecision
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import (
    get_market_regime_service,
    get_pretrade_agent,
    get_quote_service,
    get_ticker_context_service,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

_ACTION_LABEL: dict[TradeDecision, str] = {
    TradeDecision.BUY: "MUA",
    TradeDecision.SELL: "B\u00c1N",
    TradeDecision.REDUCE: "GI\u1ea2M",
    TradeDecision.HOLD: "GI\u1eee",
}

_PROCEED_META: dict[bool, dict[str, Any]] = {
    True: {"emoji": "\u2705", "label": "GO", "color": discord.Color.green()},
    False: {"emoji": "\u274c", "label": "KH\u00d4NG N\u00caN", "color": discord.Color.red()},
}

_ACTION_CHOICES = [
    app_commands.Choice(name="Mua (m\u1edf / mua th\u00eam)", value="BUY"),
    app_commands.Choice(name="B\u00e1n to\u00e0n b\u1ed9", value="SELL"),
    app_commands.Choice(name="Gi\u1ea3m t\u1ef7 tr\u1ecdng", value="REDUCE"),
    app_commands.Choice(name="Gi\u1eef (x\u00e1c nh\u1eadn n\u1eafm gi\u1eef)", value="HOLD"),
]

_ALIGNMENT_ICON: dict[AlignmentStatus, str] = {
    AlignmentStatus.ALIGNED: "\u2705",
    AlignmentStatus.NEUTRAL: "\u27a1\ufe0f",
    AlignmentStatus.MISALIGNED: "\u26a0\ufe0f",
}

_CATEGORY_ICON: dict[ResolutionCategory, str] = {
    ResolutionCategory.THESIS_CONFLICT: "\U0001f4cb",
    ResolutionCategory.RISK_LIMIT: "\U0001f6a8",
    ResolutionCategory.TIMING: "\U0001f4b0",
    ResolutionCategory.MARKET_CONDITION: "\U0001f4ca",
    ResolutionCategory.PORTFOLIO_BALANCE: "\U0001f30d",
}

_PRIORITY_BADGE = {
    "BLOCKING": "[P1]",
    "HIGH": "[P2]",
    "MEDIUM": "[P3]",
    "LOW": "[P4]",
}


class PretradeCog(BaseCog):
    @app_commands.command(
        name="pretrade",
        description="Cross-check thesis, signal, brief tr\u01b0\u1edbc khi v\u00e0o l\u1ec7nh",
    )
    @app_commands.describe(
        ticker="M\u00e3 c\u1ed5 phi\u1ebfu, VD: VCB",
        action="L\u1ec7nh s\u1eafp \u0111\u1eb7t (m\u1eb7c \u0111\u1ecbnh: Mua)",
        quantity="Kh\u1ed1i l\u01b0\u1ee3ng d\u1ef1 ki\u1ebfn (c\u1ed5 phi\u1ebfu), tu\u1ef3 ch\u1ecdn",
    )
    @app_commands.choices(action=_ACTION_CHOICES)
    async def pretrade(
        self,
        interaction: discord.Interaction,
        ticker: str,
        action: app_commands.Choice[str] | None = None,
        quantity: app_commands.Range[int, 1, 10_000_000] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=False)
        intended_action = action.value if action is not None else "BUY"

        user_id = str(interaction.user.id)
        try:
            from src.platform.db import get_session
            from src.thesis.pretrade_service import PreTradeService

            async with get_session() as session:
                svc = PreTradeService(
                    session=session,
                    quote_service=get_quote_service(),
                    pretrade_agent=get_pretrade_agent(),
                    market_regime_service=get_market_regime_service(),
                    ticker_context_service=get_ticker_context_service(),
                )
                result = await svc.check(
                    ticker=ticker,
                    user_id=user_id,
                    intended_action=intended_action,
                    quantity=quantity,
                )
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


def _clamp(text: str, limit: int = 1000) -> str:
    """Clamp embed field value dưới Discord 1024-char limit, cắt theo dòng cuối."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_nl = cut.rfind("\n")
    if last_nl > limit - 200:
        cut = cut[:last_nl]
    return cut.rstrip() + "…"


def _build_pretrade_embed(result: Any) -> discord.Embed:
    """Build Discord embed from PreTradeCheckOutput (current schema)."""
    # P1: nhãn theo proceed_recommendation; intended_action chỉ để hiển thị lệnh.
    meta = _PROCEED_META[bool(result.proceed_recommendation)]
    action_label = _ACTION_LABEL.get(result.intended_action, str(result.intended_action))
    conf_bar = "\u2588" * round(result.confidence * 10) + "\u2591" * (
        10 - round(result.confidence * 10)
    )

    # verdict (BULLISH/BEARISH/…) shown in title alongside intended_action label
    verdict_str = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)

    embed = discord.Embed(
        title=(
            f"{meta['emoji']} {action_label} {result.ticker}: {meta['label']}"
            f" \u00b7 c\u1ed5 phi\u1ebfu {verdict_str}"
        ),
        description=_clamp(result.risk_summary, 4000),
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
            value=_clamp("\n".join(f"\u2022 {c}" for c in result.blocking_issues)),
            inline=False,
        )

    # Resolution steps — khi AI không khuyến nghị thực hiện lệnh
    resolution_steps = result.resolution_steps or []
    if resolution_steps and not result.proceed_recommendation:
        steps = sorted(
            resolution_steps,
            key=lambda s: (
                list(_PRIORITY_BADGE.keys()).index(s.priority)
                if s.priority in _PRIORITY_BADGE
                else 99
            ),
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
            value=_clamp("\n".join(lines)),
            inline=False,
        )

    # Thesis alignment note
    if result.thesis_alignment_note:
        embed.add_field(
            name="\U0001f4cb Thesis",
            value=_clamp(result.thesis_alignment_note),
            inline=False,
        )

    # Sizing note
    if result.sizing_note:
        embed.add_field(
            name="\U0001f4b0 Sizing",
            value=_clamp(result.sizing_note, 500),
            inline=False,
        )

    embed.set_footer(
        text=f"\u0110\u1ed9 tin c\u1eady: {conf_bar} {result.confidence:.0%}  \u00b7  stock-agent pre-trade"
    )
    return embed
