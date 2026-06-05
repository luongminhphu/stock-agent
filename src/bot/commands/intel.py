"""Intel command — /intel slash command.

Owner: bot segment (adapter only).
No domain logic — delegates to DashboardService.get_intelligence() and renders embeds.

Usage:
    /intel — AI intelligence snapshot của portfolio (verdict, actions, risk flags).
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.bot.commands.intel_embeds import build_intel_embeds
from src.platform.logging import get_logger

logger = get_logger(__name__)


class IntelCog(BaseCog):
    """Slash command: /intel"""

    @app_commands.command(
        name="intel",
        description="AI intelligence snapshot: verdict, priority actions, risk flags",
    )
    async def intel(self, interaction: discord.Interaction) -> None:
        """Return intelligence snapshot embed. Ephemeral — only visible to caller."""
        await interaction.response.defer(ephemeral=True)

        user_id = self.user_id(interaction)

        try:
            from src.readmodel.dashboard_service import DashboardService

            svc = DashboardService()
            data = await svc.get_intelligence(user_id)
        except Exception as exc:
            logger.error("command.intel.service_error", user_id=user_id, error=str(exc))
            await self.send_error(
                interaction,
                "Intelligence không khả dụng",
                "Hệ thống chưa sẵn sàng. Thử lại sau vài giây.",
            )
            return

        if data is None:
            await self.send_info(
                interaction,
                "Chưa có intelligence snapshot",
                "Chạy `/brief` trước để tạo snapshot, hoặc chờ scheduler chạy tự động.",
            )
            logger.info("command.intel.not_found", user_id=user_id)
            return

        embeds = build_intel_embeds(data)
        await interaction.followup.send(embeds=embeds, ephemeral=True)
        logger.info(
            "command.intel.sent",
            user_id=user_id,
            verdict=data.get("overall_verdict"),
            is_stale=data.get("is_stale"),
        )
