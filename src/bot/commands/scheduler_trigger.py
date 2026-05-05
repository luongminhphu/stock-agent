"""Manual scheduler triggers — owner-only.

Owner: bot segment. Adapter only.

Commands:
  /run_replay_scheduler — trigger DecisionReplayScheduler thủ công.
  /run_snapshot         — trigger SnapshotScheduler thủ công (seed backtesting data).

Cả 2 đều gated: chỉ bot owner được dùng.
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
            scheduler = get_snapshot_scheduler()
            await scheduler._run_snapshot()  # type: ignore[union-attr]
        except Exception as exc:
            logger.error("run_snapshot.error", error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="Snapshot thất bại",
                description=f"`{exc}`",
            )
            return

        # Đọc lại số snapshot vừa ghi để báo cáo
        try:
            from datetime import UTC, datetime, timedelta

            from sqlalchemy import func, select

            from src.platform.db import AsyncSessionLocal
            from src.thesis.models import ThesisSnapshot

            cutoff = datetime.now(UTC) - timedelta(minutes=2)
            async with AsyncSessionLocal() as session:
                written = (
                    await session.scalar(
                        select(func.count(ThesisSnapshot.id)).where(
                            ThesisSnapshot.snapshotted_at >= cutoff
                        )
                    )
                ) or 0
        except Exception:
            written = -1  # không lấy được count nhưng job đã chạy

        count_text = f"**{written}** snapshot(s) vừa ghi." if written >= 0 else "Job chạy xong (không đếm được số rows)."

        embed = discord.Embed(
            title="📸 Snapshot hoàn tất",
            description=(
                f"{count_text}\n\n"
                "Tab **Backtesting** trên dashboard sẽ có data sau khi refresh."
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SchedulerTriggerCog(bot))
