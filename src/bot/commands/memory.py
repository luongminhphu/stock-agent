"""Discord /memory command group — view and refresh investor memory.

Owner: bot segment (adapter only).
All domain logic lives in:
  - src.ai.memory.memory_service  (MemoryService.get_memory_context)
  - src.ai.memory.repository      (MemorySnapshotRepository)
  - src.ai.memory.consolidator    (MemoryConsolidator.synthesize_patterns)

Commands:
  /memory view    — display latest MemorySnapshot + pattern synthesis block.
  /memory refresh — trigger on-demand pattern synthesis and show result.

Design rules:
  - Thin adapter: no business logic here.
  - Both subcommands are ephemeral (private to the invoking user).
  - Failures surface as user-friendly error embeds, never tracebacks.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.logging import get_logger

logger = get_logger(__name__)


class MemoryCog(BaseCog):
    """Slash command group: /memory."""

    memory_group = app_commands.Group(
        name="memory",
        description="🧠 Xem và cập nhật bộ nhớ đầu tư của bạn",
    )

    @memory_group.command(
        name="view",
        description="Xem bộ nhớ hiện tại: pattern, bias, memory snapshot.",
    )
    async def memory_view(self, interaction: discord.Interaction) -> None:
        await self.defer(interaction, ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                # --- Episodic + snapshot render ---
                from src.ai.memory.memory_service import MemoryService
                mem_ctx = await MemoryService.get_memory_context(
                    session, user_id=user_id
                )

                # --- Pattern synthesis block from latest snapshot ---
                from src.ai.memory.repository import MemorySnapshotRepository
                snapshot_repo = MemorySnapshotRepository(session)
                latest = await snapshot_repo.get_latest(user_id=user_id)

            if mem_ctx.is_empty() and latest is None:
                await self.send_info(
                    interaction,
                    title="🧠 Chưa có bộ nhớ",
                    description=(
                        "Chưa có đủ dữ liệu để hiển thị bộ nhớ.\n"
                        "Hệ thống sẽ tự động tích lũy sau khi bạn sử dụng các tính năng "
                        "phân tích (briefing, thesis, watchlist scan...)."
                    ),
                )
                return

            # Build embed
            embed = discord.Embed(
                title="🧠 Bộ nhớ đầu tư",
                color=discord.Color.purple(),
            )

            # Episodic/snapshot section
            if not mem_ctx.is_empty():
                rendered = mem_ctx.render()
                if len(rendered) > 1024:
                    rendered = rendered[:1021] + "..."
                embed.add_field(
                    name="📊 Memory context",
                    value=rendered or "*(empty)*",
                    inline=False,
                )

            # Pattern synthesis from stored snapshot blob
            if latest is not None:
                behavioral = getattr(latest, "behavioral_patterns", None)
                pattern_block = ""
                if behavioral:
                    try:
                        import json
                        from src.ai.memory.consolidator import PatternSynthesisOutput
                        stored = json.loads(behavioral)
                        synth = PatternSynthesisOutput(**stored)
                        pattern_block = synth.to_prompt_block()
                    except Exception:
                        pass

                if pattern_block:
                    if len(pattern_block) > 1024:
                        pattern_block = pattern_block[:1021] + "..."
                    embed.add_field(
                        name="🔍 Patterns & Bias warnings",
                        value=f"```{pattern_block}```",
                        inline=False,
                    )

                # Snapshot metadata footer
                period_end = getattr(latest, "period_end", None)
                episode_count = getattr(latest, "episode_count", None)
                footer_parts = []
                if period_end:
                    footer_parts.append(
                        f"Cập nhật: {period_end.strftime('%d/%m/%Y %H:%M')}"
                    )
                if episode_count is not None:
                    footer_parts.append(f"{episode_count} episodes")
                if footer_parts:
                    embed.set_footer(text=" • ".join(footer_parts))

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("memory_view.failed", user_id=user_id, error=str(exc))
            await self.send_error(
                interaction,
                title="Lỗi khi tải bộ nhớ",
                description=f"Không thể tải bộ nhớ: `{exc}`",
            )

    @memory_group.command(
        name="refresh",
        description="Cưỡng buộc tổng hợp lại patterns từ episodic memory.",
    )
    async def memory_refresh(self, interaction: discord.Interaction) -> None:
        await self.defer(interaction, ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                from src.ai.client import AIClient
                from src.ai.memory.consolidator import MemoryConsolidator

                consolidator = MemoryConsolidator(
                    client=AIClient(), user_id=user_id
                )
                output = await consolidator.synthesize_patterns(session)

            if output is None:
                await self.send_warning(
                    interaction,
                    title="Chưa đủ dữ liệu",
                    description=(
                        "Cần tối thiểu **5 AI interactions** trong 14 ngày gần nhất "
                        "để tổng hợp patterns.\n"
                        "Tiếp tục sử dụng các tính năng phân tích và thử lại sau."
                    ),
                )
                return

            # Build result embed
            embed = discord.Embed(
                title="🧠 Patterns đã được tổng hợp",
                color=discord.Color.purple(),
            )

            # Confidence indicator
            conf_pct = f"{output.confidence:.0%}"
            conf_label = (
                "🟢 Cao" if output.confidence >= 0.7
                else "🟡 Trung bình" if output.confidence >= 0.5
                else "🔴 Thấp (chưa đủ độ tin cậy)"
            )
            embed.add_field(
                name="📊 Độ tin cậy",
                value=f"{conf_label} ({conf_pct})",
                inline=False,
            )

            # Patterns
            if output.patterns:
                patterns_text = "\n".join(f"• {p}" for p in output.patterns)
                if len(patterns_text) > 1024:
                    patterns_text = patterns_text[:1021] + "..."
                embed.add_field(
                    name="🔄 Patterns",
                    value=patterns_text,
                    inline=False,
                )
            else:
                embed.add_field(
                    name="🔄 Patterns",
                    value="*(không nhận diện được pattern rõ ràng)*",
                    inline=False,
                )

            # Bias warnings
            if output.bias_warnings:
                warnings_text = "\n".join(f"⚠️ {w}" for w in output.bias_warnings)
                if len(warnings_text) > 1024:
                    warnings_text = warnings_text[:1021] + "..."
                embed.add_field(
                    name="🧠 Bias warnings",
                    value=warnings_text,
                    inline=False,
                )

            # Market regime history
            if output.market_regime_reads:
                embed.add_field(
                    name="🌏 Regime history",
                    value=" | ".join(output.market_regime_reads),
                    inline=False,
                )

            embed.set_footer(
                text="Patterns đã được lưu vào bộ nhớ — sẽ được inject vào các AI call tiếp theo."
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("memory_refresh.failed", user_id=user_id, error=str(exc))
            await self.send_error(
                interaction,
                title="Lỗi khi refresh bộ nhớ",
                description=f"Không thể tổng hợp patterns: `{exc}`",
            )
