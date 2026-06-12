"""Manual scheduler triggers — owner-only.

Owner: bot segment. Adapter only.

Commands:
  /run_replay_scheduler  — trigger DecisionReplayScheduler thủ công.
  /run_snapshot          — trigger SnapshotScheduler thủ công (seed backtesting data).
  /run_intelligence      — trigger Intelligence Engine thủ công (phân tích + khuyến nghị).

Tất cả đều gated: chỉ bot owner được dùng.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.logging import get_logger

logger = get_logger(__name__)


class SchedulerTriggerCog(BaseCog):
    """Owner-only commands to manually trigger schedulers."""

    # ------------------------------------------------------------------
    # /run_replay_scheduler
    # ------------------------------------------------------------------

    @app_commands.command(
        name="run_replay_scheduler",
        description="[Owner] Chạy DecisionReplayScheduler thủ công",
    )
    async def run_replay_scheduler(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        app_info = await self.bot.application_info()
        if interaction.user.id != app_info.owner.id:
            await self.send_error(
                interaction,
                title="Không có quyền",
                description="Lệnh này chỉ dành cho bot owner.",
            )
            return

        try:
            from src.platform.bootstrap import get_quote_service, get_replay_agent
            from src.platform.db import get_session
            from src.thesis.decision_replay_scheduler import DecisionReplayScheduler
            from src.thesis.decision_service import DecisionService

            async with get_session() as session:
                svc = DecisionService(
                    session=session,
                    quote_service=get_quote_service(),
                    replay_agent=get_replay_agent(),
                )
                scheduler = DecisionReplayScheduler(svc)
                results = await scheduler.run_pending()

        except Exception as exc:
            logger.error("run_replay_scheduler.error", error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="Scheduler thất bại",
                description=f"`{exc}`",
            )
            return

        processed = len(results)
        if processed == 0:
            await self.send_info(
                interaction,
                title="✅ Scheduler chạy xong",
                description="Không có decision nào đến hạn.",
            )
            return

        lines = []
        for env in results:
            verdict = env.outcome_verdict or "?"
            replay = env.replay
            lesson_preview = ""
            if replay is not None:
                lesson = getattr(replay, "key_lesson", None)
                if lesson:
                    lesson_preview = f" → _{lesson[:80]}{'...' if len(lesson) > 80 else ''}_"
            lines.append(f"• `#{env.decision_id}` **{env.ticker}** [{verdict}]{lesson_preview}")

        body, footer = self.paginate_lines(lines)
        embed = discord.Embed(
            title=f"🔄 Replay Scheduler — {processed} decision(s) processed",
            description=body,
            color=discord.Color.blurple(),
        )
        if footer:
            embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /run_snapshot
    # ------------------------------------------------------------------

    @app_commands.command(
        name="run_snapshot",
        description="[Owner] Chụp giá + ghi ThesisSnapshot thủ công — seed data cho Backtesting",
    )
    async def run_snapshot(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        app_info = await self.bot.application_info()
        if interaction.user.id != app_info.owner.id:
            await self.send_error(
                interaction,
                title="Không có quyền",
                description="Lệnh này chỉ dành cho bot owner.",
            )
            return

        try:
            from src.platform.bootstrap import get_snapshot_scheduler
            written = await get_snapshot_scheduler().run_once()
        except Exception as exc:
            logger.error("run_snapshot.error", error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="Snapshot thất bại",
                description=f"`{exc}`",
            )
            return

        embed = discord.Embed(
            title="📸 Snapshot hoàn tất",
            description=(
                f"**{written}** snapshot(s) vừa ghi.\n\n"
                "Tab **Backtesting** trên dashboard sẽ có data sau khi refresh."
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


    # ------------------------------------------------------------------
    # /run_intelligence
    # ------------------------------------------------------------------

    @app_commands.command(
        name="run_intelligence",
        description="[Owner] Chạy Intelligence Engine thủ công — cập nhật phân tích + khuyến nghị",
    )
    async def run_intelligence(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        app_info = await self.bot.application_info()
        if interaction.user.id != app_info.owner.id:
            await self.send_error(
                interaction,
                title="Không có quyền",
                description="Lệnh này chỉ dành cho bot owner.",
            )
            return

        try:
            from src.platform.event_bus import get_event_bus
            from src.platform.events import IntelligenceEngineRequestedEvent
            from src.platform.settings import get_settings

            cfg = get_settings()
            user_id = getattr(cfg, "scheduler_user_id", None)
            if not user_id:
                await self.send_error(
                    interaction,
                    title="Thiếu cấu hình",
                    description="`scheduler_user_id` chưa được cấu hình trong settings.",
                )
                return

            bus = get_event_bus()
            await bus.publish(
                IntelligenceEngineRequestedEvent(
                    user_id=str(user_id),
                    trigger_type="manual",
                    trigger_source="manual",
                )
            )
            logger.info(
                "command.run_intelligence.event_emitted",
                user_id=str(user_id),
                triggered_by=interaction.user.id,
            )

        except Exception as exc:
            logger.error("command.run_intelligence.error", error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="Thất bại",
                description=f"`{exc}`",
            )
            return

        embed = discord.Embed(
            title="🧠 Intelligence Engine — Đã kích hoạt",
            description=(
                "Engine đang chạy trong nền.\n\n"
                "• Kết quả sẽ xuất hiện trên dashboard sau vài giây.\n"
                "• Discord embed sẽ gửi vào channel sau khi hoàn thành."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SchedulerTriggerCog(bot))
