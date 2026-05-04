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
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.logging import get_logger

logger = get_logger(__name__)

_VERDICT_META = {
    "CORRECT":   {"emoji": "✅", "color": discord.Color.green()},
    "INCORRECT": {"emoji": "❌", "color": discord.Color.red()},
    "MIXED":     {"emoji": "⚖️", "color": discord.Color.orange()},
}
_DEFAULT_COLOR = {"emoji": "📋", "color": discord.Color.blue()}

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
                title="Không thể ghi quyết định",
                description=str(exc),
            )
            return
        except Exception as exc:
            logger.error("log_decision.command.error", error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="Lỗi hệ thống",
                description=f"Không thể lưu quyết định.\n`{exc}`",
            )
            return

        price_str = self.fmt_vnd(row.price_at_decision)
        score_str = f"{row.thesis_score_at_decision:.1f}" if row.thesis_score_at_decision else "N/A"

        embed = discord.Embed(
            title=f"📝 Đã ghi: {action} {row.ticker}",
            description=f"> {rationale}",
            color=discord.Color.green(),
        )
        embed.add_field(name="📌 Decision ID", value=f"`#{row.id}`", inline=True)
        embed.add_field(name="💰 Giá lúc vào", value=price_str, inline=True)
        embed.add_field(name="📊 Thesis score", value=score_str, inline=True)
        embed.set_footer(
            text=f"Outcome sẽ được evaluate sau {horizon_days} ngày  ·  stock-agent"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /replay
    # ------------------------------------------------------------------

    @app_commands.command(
        name="replay",
        description="Replay AI phân tích outcome của một quyết định đã qua horizon",
    )
    @app_commands.describe(
        decision_id="ID của quyết định (xem trong /log_decision hoặc /decision_history)",
    )
    async def replay(
        self,
        interaction: discord.Interaction,
        decision_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            from src.platform.bootstrap import get_quote_service, get_replay_agent
            from src.platform.db import get_session
            from src.thesis.decision_service import DecisionService

            async with get_session() as session:
                svc = DecisionService(
                    session=session,
                    quote_service=get_quote_service(),
                    replay_agent=get_replay_agent(),
                )
                # Evaluate nếu chưa có outcome
                from sqlalchemy import select
                from src.thesis.models import DecisionLog
                stmt = select(DecisionLog).where(DecisionLog.id == decision_id)
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is None:
                    await self.send_error(
                        interaction,
                        title="Không tìm thấy",
                        description=f"Decision `#{decision_id}` không tồn tại.",
                    )
                    return

                if row.user_id != self.user_id(interaction):
                    await self.send_error(
                        interaction,
                        title="Không có quyền",
                        description="Decision này không thuộc về bạn.",
                    )
                    return

                if row.outcome_evaluated_at is None:
                    await svc.evaluate_outcome(decision_id)

                envelope = await svc.analyze_decision(decision_id)

                if envelope.replay is not None:
                    await svc.persist_lesson(
                        decision_id,
                        key_lesson=getattr(envelope.replay, "key_lesson", None),
                        pattern_detected=getattr(envelope.replay, "pattern_detected", None),
                    )

        except ValueError as exc:
            await self.send_error(
                interaction,
                title="Không thể replay",
                description=str(exc),
            )
            return
        except Exception as exc:
            logger.error("replay.command.error", decision_id=decision_id, error=str(exc), exc_info=True)
            await self.send_error(
                interaction,
                title="Lỗi hệ thống",
                description=f"Replay thất bại.\n`{exc}`",
            )
            return

        embed = _build_replay_embed(decision_id, envelope)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /lessons
    # ------------------------------------------------------------------

    @app_commands.command(
        name="lessons",
        description="Xem tổng hợp bài học AI rút ra từ các quyết định đã replay",
    )
    @app_commands.describe(
        ticker="Lọc theo mã cổ phiếu (tuỳ chọn, ví dụ: VCB)",
        limit="Số bài học muốn xem (1–50, mặc định 10)",
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
                title="Lỗi hệ thống",
                description=f"Không thể tải lessons.\n`{exc}`",
            )
            return

        embed = _build_lessons_embed(rows, ticker=ticker, limit=limit)
        await interaction.followup.send(embed=embed, ephemeral=True)


def _build_replay_embed(decision_id: int, envelope) -> discord.Embed:
    """Build Discord embed for replay result."""
    verdict = envelope.outcome_verdict or "MIXED"
    meta = _VERDICT_META.get(verdict, _DEFAULT_COLOR)
    replay = envelope.replay

    title = f"{meta['emoji']} Replay #{decision_id} — {envelope.ticker} [{verdict}]"

    if replay is None:
        return discord.Embed(
            title=title,
            description="ReplayAgent không khả dụng. Outcome đã được evaluate.",
            color=meta["color"],
        )

    embed = discord.Embed(title=title, color=meta["color"])

    what_right = getattr(replay, "what_went_right", None)
    what_wrong = getattr(replay, "what_went_wrong", None)
    key_lesson = getattr(replay, "key_lesson", None)
    pattern = getattr(replay, "pattern_detected", None)
    adjustment = getattr(replay, "suggested_adjustment", None)
    confidence = getattr(replay, "confidence", None)

    if what_right:
        embed.add_field(name="✅ Đúng ở điểm nào", value=what_right, inline=False)
    if what_wrong:
        embed.add_field(name="❌ Sai ở điểm nào", value=what_wrong, inline=False)
    if key_lesson:
        embed.add_field(name="💡 Key lesson", value=key_lesson, inline=False)
    if pattern:
        embed.add_field(name="🔁 Pattern", value=f"`{pattern}`", inline=True)
    if adjustment:
        embed.add_field(name="🎯 Điều chỉnh gợi ý", value=adjustment, inline=False)

    if confidence is not None:
        conf_bar = "█" * round(confidence * 10) + "░" * (10 - round(confidence * 10))
        embed.set_footer(text=f"Confidence: {conf_bar} {confidence:.0%}  ·  stock-agent replay")

    return embed


def _build_lessons_embed(
    rows: list,
    *,
    ticker: str | None,
    limit: int,
) -> discord.Embed:
    """Build Discord embed listing AI-generated lessons."""
    title = f"🧠 Lessons — {ticker.upper()}" if ticker else "🧠 Lessons — Tất cả mã"

    if not rows:
        scope = f"mã **{ticker.upper()}**" if ticker else "bất kỳ mã nào"
        embed = discord.Embed(
            title=title,
            description=(
                f"Chưa có bài học nào được ghi nhận cho {scope}.\n"
                "Dùng `/replay <decision_id>` sau khi horizon qua để AI phân tích."
            ),
            color=discord.Color.greyple(),
        )
        embed.set_footer(text="stock-agent lessons")
        return embed

    embed = discord.Embed(title=title, color=discord.Color.blue())

    for row in rows:
        verdict = row.outcome_verdict or "?"
        meta = _VERDICT_META.get(verdict, _DEFAULT_COLOR)
        date_str = row.decision_at.strftime("%d/%m/%Y") if row.decision_at else "N/A"
        pattern_str = f"  `{row.pattern_detected}`" if row.pattern_detected else ""
        field_name = (
            f"{meta['emoji']} #{row.id} · {row.ticker} · "
            f"{row.decision_type} · {date_str}{pattern_str}"
        )
        embed.add_field(
            name=field_name,
            value=row.key_lesson,
            inline=False,
        )

    embed.set_footer(
        text=f"Hiển thị {len(rows)}/{limit} bài học mới nhất  ·  stock-agent"
    )
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DecisionCog(bot))
