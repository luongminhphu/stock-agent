"""ConvictionTimelineCog — /conviction <ticker> slash command.

Owner: bot segment.
Pure adapter: reads from ThesisTimelineService, formats via conviction_timeline_embeds.
No domain logic. No direct DB writes.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.conviction_timeline_embeds import (
    build_conviction_embed,
    build_conviction_not_found_embed,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class ConvictionTimelineCog(commands.Cog, name="conviction"):
    """Slash command: /conviction <ticker> [limit]"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="conviction",
        description="Xem Conviction Score Timeline của một thesis theo mã cổ phiếu",
    )
    @app_commands.describe(
        ticker="Mã cổ phiếu (vd: VCB, VNM, HPG)",
        limit="Số snapshot tối đa hiển thị (mặc định 20)",
    )
    async def conviction(
        self,
        interaction: discord.Interaction,
        ticker: str,
        limit: app_commands.Range[int, 5, 50] = 20,
    ) -> None:
        await interaction.response.defer(thinking=True)

        ticker_upper = ticker.upper().strip()
        logger.info("conviction.command.called", ticker=ticker_upper, limit=limit)

        try:
            result = await self._fetch_timeline(ticker_upper, limit)
        except Exception:
            logger.exception("conviction.command.error", ticker=ticker_upper)
            await interaction.followup.send(
                "❌ Có lỗi khi lấy dữ liệu conviction. Vui lòng thử lại sau.",
                ephemeral=True,
            )
            return

        if result is None or result.total == 0:
            embed = build_conviction_not_found_embed(ticker_upper)
        else:
            embed = build_conviction_embed(result)

        await interaction.followup.send(embed=embed)

    async def _fetch_timeline(self, ticker: str, limit: int):
        """Query ThesisTimelineService for the conviction timeline.

        Opens a short-lived AsyncSession via platform session factory.
        Returns ConvictionTimelineResponse | None.

        Strategy: find the most-recently-created ACTIVE thesis for this ticker,
        then call get_conviction_timeline(thesis_id, limit).
        Returns None if no matching thesis found.
        """
        from sqlalchemy import select

        from src.platform.bootstrap import get_async_session_factory
        from src.readmodel.timeline_service import ThesisTimelineService
        from src.thesis.models import Thesis, ThesisStatus

        session_factory = get_async_session_factory()

        async with session_factory() as session:
            # Find latest active thesis for this ticker
            result = await session.execute(
                select(Thesis)
                .where(
                    Thesis.ticker == ticker,
                    Thesis.status == ThesisStatus.ACTIVE,
                )
                .order_by(Thesis.created_at.desc())
                .limit(1)
            )
            thesis = result.scalar_one_or_none()

            if thesis is None:
                logger.info("conviction.no_active_thesis", ticker=ticker)
                return None

            svc = ThesisTimelineService(session)
            return await svc.get_conviction_timeline(thesis.id, limit=limit)
