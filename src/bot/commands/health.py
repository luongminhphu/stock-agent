"""Health command — /health slash command.

Owner: bot segment (adapter only).
No domain logic — reads from SchedulerMonitor singleton and renders embed.

Usage:
    /health — show health status of all scheduled tasks (ephemeral).
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.platform.logging import get_logger
from src.platform.scheduler_monitor import get_monitor

logger = get_logger(__name__)


class HealthCog(BaseCog):
    """Slash command: /health"""

    @app_commands.command(name="health", description="Kiểm tra trạng thái hệ thống và scheduled tasks")
    async def health(self, interaction: discord.Interaction) -> None:
        """Return a health embed for all scheduler tasks. Ephemeral — only visible to caller."""
        await interaction.response.defer(ephemeral=True)

        try:
            monitor = get_monitor()
            embed = monitor.get_health_embed()
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(
                "command.health",
                user_id=interaction.user.id,
            )
        except Exception as exc:
            logger.error("command.health.error", error=str(exc))
            await interaction.followup.send(
                "❌ Không thể lấy trạng thái hệ thống. Kiểm tra log.",
                ephemeral=True,
            )
