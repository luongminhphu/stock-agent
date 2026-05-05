"""Decision commands — /log_decision, /replay and /lessons.

Owner: bot segment. Adapter only — no domain logic.

Commands:
  /log_decision  <thesis_id> <action> <rationale> [horizon]
      Log a BUY/SELL/HOLD/ADD/REDUCE decision with frozen context.

  /replay  <decision_id>
      Run AI replay on a decision whose horizon has passed and
      outcome has been evaluated. Surfaces key_lesson + pattern.

  /lessons  [ticker] [limit]
      Show a summary of AI-generated lessons from past decisions.

All domain logic lives in src/thesis/decision_service.py.
All embed builders live in src/bot/commands/decision_embeds.py.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.bot.commands.decision_embeds import (
    build_lessons_embed,
    build_single_replay_embed,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

_VALID_ACTIONS = ["BUY", "SELL", "HOLD", "ADD", "REDUCE"]


class DecisionCog(BaseCog):
    """Commands for logging decisions and replaying outcomes."""

    # ------------------------------------------------------------------
    # /log_decision
    # ------------------------------------------------------------------

    @app_commands.command(
        name="log_decision",
        description="Ghi lại quyết định BUY/SELL/HOLD/ADD/REDUCE vào thesis",
    )
    @app_commands.describe(
        thesis_id="ID của thesis (dùng /thesis list để xem)",
        action="Loại quyết định: BUY, SELL, HOLD, ADD, REDUCE",
        rationale="Lý do quyết định (tối đa 500 ký tự)",
        horizon_days="Số ngày để evaluate outcome (mặc định: 30)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name=a, value=a) for a in _VALID_ACTIONS
    ])
    async def log_decision(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
        action: str,
        rationale: str,
        horizon_days: int = 30,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            from src.platform.bootstrap import get_quote_service
            from src.platform.db import get_session
            from src.thesis.decision_service import DecisionService

            async with get_session() as session:
                svc = DecisionService(
                    session=session,
                    quote_service=get_quote_service(),
                )
                row = await svc.log_decision(
                    thesis_id=thesis_id,
                    user_id=user_id,
                    decision_type=action,
                    rationale=rationale,
                    review_horizon_days=horizon_days,
                )
        except ValueError as exc:
            await self.send_error(
                interaction,
                title="Kh\u00f4ng th\u1ec3 ghi quy\u1ebft \u0111\u1ecbnh",
                description=str(exc),
            )
            return
        except Exception as exc:
            logger.error("log_decision.command.error", error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="L\u1ed7i h\u1ec7 th\u1ed1ng",
                description=f"Kh\u00f4ng th\u1ec3 l\u01b0u quy\u1ebft \u0111\u1ecbnh.\n`{exc}`",
            )
            return

        price_str = self.fmt_vnd(row.price_at_decision)
        score_str = f"{row.thesis_score_at_decision:.1f}" if row.thesis_score_at_decision else "N/A"

        embed = discord.Embed(
            title=f"\U0001f4dd \u0110\u00e3 ghi: {action} {row.ticker}",
            description=f"> {rationale}",
            color=discord.Color.green(),
        )
        embed.add_field(name="\U0001f4cc Decision ID", value=f"`#{row.id}`", inline=True)
        embed.add_field(name="\U0001f4b0 Gi\u00e1 l\u00fac v\u00e0o", value=price_str, inline=True)
        embed.add_field(name="\U0001f4ca Thesis score", value=score_str, inline=True)
        embed.set_footer(
            text=f"Outcome s\u1ebd \u0111\u01b0\u1ee3c evaluate sau {horizon_days} ng\u00e0y  \u00b7  stock-agent"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /replay
    # ------------------------------------------------------------------

    @app_commands.command(
        name="replay",
        description="Replay AI ph\u00e2n t\u00edch outcome c\u1ee7a m\u1ed9t quy\u1ebft \u0111\u1ecbnh \u0111\u00e3 qua horizon",
    )
    @app_commands.describe(
        decision_id="ID c\u1ee7a quy\u1ebft \u0111\u1ecbnh (xem trong /log_decision ho\u1eb7c /decision_history)",
    )
    async def replay(
        self,
        interaction: discord.Interaction,
        decision_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            from src.platform.bootstrap import get_quote_service, get_replay_agent
            from src.platform.db import get_session
            from src.thesis.decision_service import DecisionNotFoundError, DecisionService

            async with get_session() as session:
                svc = DecisionService(
                    session=session,
                    quote_service=get_quote_service(),
                    replay_agent=get_replay_agent(),
                )
                envelope = await svc.replay_decision(decision_id, user_id=user_id)

        except DecisionNotFoundError as exc:
            await self.send_error(
                interaction,
                title="Kh\u00f4ng t\u00ecm th\u1ea5y",
                description=str(exc),
            )
            return
        except ValueError as exc:
            await self.send_error(
                interaction,
                title="Kh\u00f4ng th\u1ec3 replay",
                description=str(exc),
            )
            return
        except Exception as exc:
            logger.error("replay.command.error", decision_id=decision_id, error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="L\u1ed7i h\u1ec7 th\u1ed1ng",
                description=f"Replay th\u1ea5t b\u1ea1i.\n`{exc}`",
            )
            return

        embed = build_single_replay_embed(decision_id, envelope)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /lessons
    # ------------------------------------------------------------------

    @app_commands.command(
        name="lessons",
        description="Xem t\u1ed5ng h\u1ee3p b\u00e0i h\u1ecdc AI r\u00fat ra t\u1eeb c\u00e1c quy\u1ebft \u0111\u1ecbnh \u0111\u00e3 replay",
    )
    @app_commands.describe(
        ticker="L\u1ecdc theo m\u00e3 c\u1ed5 phi\u1ebfu (tu\u1ef3 ch\u1ecdn, v\u00ed d\u1ee5: VCB)",
        limit="S\u1ed1 b\u00e0i h\u1ecdc mu\u1ed1n xem (1\u201350, m\u1eb7c \u0111\u1ecbnh 10)",
    )
    async def lessons(
        self,
        interaction: discord.Interaction,
        ticker: str | None = None,
        limit: int = 10,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            from src.platform.db import get_session
            from src.thesis.decision_service import DecisionService

            async with get_session() as session:
                svc = DecisionService(session=session)
                rows = await svc.list_lessons(
                    user_id,
                    ticker=ticker,
                    limit=limit,
                )
        except Exception as exc:
            logger.error("lessons.command.error", error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="L\u1ed7i h\u1ec7 th\u1ed1ng",
                description=f"Kh\u00f4ng th\u1ec3 t\u1ea3i lessons.\n`{exc}`",
            )
            return

        embed = build_lessons_embed(rows, ticker=ticker, limit=limit)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DecisionCog(bot))
