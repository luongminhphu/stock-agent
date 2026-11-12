"""Discord bot application factory.

Owner: bot segment.
This module only wires the runtime — no business logic here.
All domain operations are delegated to segment services.
"""

from __future__ import annotations

import asyncio
import traceback as tb

import discord
from discord.ext import commands

from src.platform.bootstrap import bootstrap, shutdown
from src.platform.config import settings
from src.platform.logging import get_logger

logger = get_logger(__name__)


def create_bot() -> commands.Bot:
    """Create and configure the Discord bot instance."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.reactions = True

    bot = commands.Bot(
        command_prefix="/",
        intents=intents,
        help_command=None,
    )

    @bot.event
    async def on_ready() -> None:
        if getattr(bot, "_stock_agent_ready", False):
            logger.warning(
                "bot.on_ready.skip",
                reason="already_initialized",
                user=str(bot.user),
            )
            return
        bot._stock_agent_ready = True  # type: ignore[attr-defined]

        try:
            await bootstrap()
            _inject_briefing_listener(bot)
            await _register_cogs(bot)
            await _sync_tree(bot)

            _start_briefing_scheduler(bot)
            _start_scan_scheduler(bot)
            _start_thesis_maintenance_scheduler(bot)
            _start_snapshot_scheduler()
            _start_reminder_scheduler(bot)
            _start_outcome_filler_scheduler(bot)
            _start_decision_replay_scheduler(bot)
            _start_memory_consolidator_scheduler(bot)
            _start_proactive_watch_scheduler(bot)
            _start_proactive_watch_subscriber(bot)
            _start_post_mortem_subscriber(bot)
            _start_signal_reaction_listener(bot)
            _start_invalidation_subscriber(bot)
            _start_position_risk_subscriber(bot)  # portfolio loss threshold → Discord
            _start_portfolio_snapshot_scheduler(bot)  # portfolio snapshot 08:15 ICT
            _start_investor_profile_scheduler(bot)  # investor profile snapshot 08:20 ICT
            _start_eod_close_capture_scheduler(bot)  # giá đóng cửa 14:50 + 15:05 ICT
            _start_plan_check_scheduler(bot)  # kế hoạch nắm giữ 15:30 ICT
            _start_deriv_sim_scheduler(bot)  # giả lập HĐTL VN30, 5 phút trong giờ GD
            _start_eod_portfolio_snapshot_scheduler(bot)  # EOD P&L snapshot 15:20 ICT
            _wire_feedback_loop_monitor(
                bot
            )  # FeedbackLoopMonitor — sliding-window error rate alert
            logger.info(
                "bot.ready",
                user=str(bot.user),
                guild_count=len(bot.guilds),
            )
        except Exception as exc:
            logger.exception(
                "bot.on_ready.failed",
                error=str(exc),
                traceback=tb.format_exc(),
            )
            raise

    @bot.event
    async def on_close() -> None:
        await shutdown()
        logger.info("bot.closed")

    @bot.event
    async def on_error(event: str, *args: object, **kwargs: object) -> None:
        logger.error(
            "bot.event_error",
            event_name=event,
            traceback=tb.format_exc(),
        )

    return bot


def run() -> None:
    bot = create_bot()
    asyncio.run(bot.start(settings.discord_token))


async def _sync_tree(bot: commands.Bot) -> None:
    if settings.discord_guild_id:
        guild = discord.Object(id=int(settings.discord_guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        logger.info("bot.tree.synced", mode="guild", guild_id=settings.discord_guild_id)
    else:
        await bot.tree.sync()
        logger.info("bot.tree.synced", mode="global")


async def _register_cogs(bot: commands.Bot) -> None:
    """Load all command cogs. Add new cogs here only — no logic."""
    from src.bot.commands.briefing import BriefingCog
    from src.bot.commands.conviction_timeline import ConvictionTimelineCog
    from src.bot.commands.decision import DecisionCog
    from src.bot.commands.health import HealthCog
    from src.bot.commands.help import HelpCog
    from src.bot.commands.market import MarketCog
    from src.bot.commands.memory import MemoryCog
    from src.bot.commands.portfolio import PortfolioCog
    from src.bot.commands.pretrade import PretradeCog
    from src.bot.commands.reviews import ReviewsCog
    from src.bot.commands.scheduler_trigger import SchedulerTriggerCog
    from src.bot.commands.thesis_crud import ThesisCrudCog
    from src.bot.commands.thesis_review import ThesisReviewCog
    from src.bot.commands.watchlist import WatchlistCog

    await bot.add_cog(WatchlistCog(bot))
    await bot.add_cog(ThesisCrudCog(bot))
    await bot.add_cog(ThesisReviewCog(bot))
    await bot.add_cog(MarketCog(bot))
    await bot.add_cog(BriefingCog(bot))
    await bot.add_cog(HelpCog(bot))
    await bot.add_cog(PretradeCog(bot))
    await bot.add_cog(ConvictionTimelineCog(bot))
    await bot.add_cog(PortfolioCog(bot))
    await bot.add_cog(DecisionCog(bot))
    await bot.add_cog(SchedulerTriggerCog(bot))
    await bot.add_cog(HealthCog(bot))
    await bot.add_cog(MemoryCog(bot))
    await bot.add_cog(ReviewsCog(bot))
    logger.info(
        "bot.cogs_loaded",
        cogs=[
            "WatchlistCog",
            "ThesisCrudCog",
            "ThesisReviewCog",
            "MarketCog",
            "BriefingCog",
            "HelpCog",
            "PretradeCog",
            "ConvictionTimelineCog",
            "PortfolioCog",
            "DecisionCog",
            "SchedulerTriggerCog",
            "HealthCog",
            "MemoryCog",
            "ReviewsCog",
        ],
    )


def _inject_briefing_listener(bot: commands.Bot) -> None:
    from src.platform.bootstrap import get_briefing_listener

    listener = get_briefing_listener()
    if listener is not None:
        listener.set_client(bot)
        logger.info("bot.briefing_listener.client_injected")
    else:
        logger.error(
            "bot.briefing_listener.not_available",
            reason="scheduler_user_id not configured — BriefingListener skipped at bootstrap.",
        )


def _start_briefing_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import BriefingScheduler

    scheduler = BriefingScheduler(bot)
    scheduler.start()


def _start_scan_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import WatchlistScanScheduler

    scheduler = WatchlistScanScheduler(bot)
    scheduler.start()


def _start_thesis_maintenance_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ThesisMaintenanceScheduler

    scheduler = ThesisMaintenanceScheduler(bot)
    scheduler.start()


def _start_snapshot_scheduler() -> None:
    from src.platform.bootstrap import get_snapshot_scheduler

    scheduler = get_snapshot_scheduler()
    if scheduler is None:
        logger.warning(
            "bot.snapshot_scheduler.not_available",
            reason="get_snapshot_scheduler() returned None",
        )
        return
    scheduler.start()


def _start_reminder_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ReminderScheduler

    scheduler = ReminderScheduler(bot)
    scheduler.start()


def _start_outcome_filler_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import OutcomeFillerScheduler

    scheduler = OutcomeFillerScheduler(bot)
    scheduler.start()


def _start_decision_replay_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import DecisionReplayScheduler

    scheduler = DecisionReplayScheduler(bot)
    scheduler.start()


def _start_memory_consolidator_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import MemoryConsolidatorScheduler
    from src.platform.bootstrap import get_memory_consolidator

    if get_memory_consolidator() is None:
        logger.warning(
            "bot.memory_consolidator_scheduler.not_available",
            reason="MemoryConsolidator not initialised",
        )
        return

    scheduler = MemoryConsolidatorScheduler(bot)
    scheduler.start()


def _start_proactive_watch_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ProactiveWatchScheduler

    scheduler = ProactiveWatchScheduler(bot)
    scheduler.start()


def _start_proactive_watch_subscriber(bot: commands.Bot) -> None:
    from src.bot.proactive_watch_subscriber import ProactiveWatchSubscriber

    channel_id = settings.alert_channel_id
    if not channel_id:
        logger.warning(
            "bot.proactive_watch_subscriber.not_available",
            reason="alert_channel_id not configured",
        )
        return

    subscriber = ProactiveWatchSubscriber(channel_id=int(channel_id))
    subscriber.set_client(bot)
    subscriber.register()
    logger.info("bot.proactive_watch_subscriber.registered", channel_id=channel_id)


def _start_post_mortem_subscriber(bot: commands.Bot) -> None:
    from src.bot.post_mortem_subscriber import PostMortemSubscriber

    channel_id = getattr(settings, "decision_channel_id", None) or getattr(
        settings, "morning_channel_id", None
    )
    if not channel_id:
        logger.warning(
            "bot.post_mortem_subscriber.not_available",
            reason="decision_channel_id and morning_channel_id not configured — post-mortem embeds disabled",
        )
        return

    subscriber = PostMortemSubscriber(channel_id=int(channel_id))
    subscriber.set_client(bot)
    subscriber.register()
    logger.info("bot.post_mortem_subscriber.registered", channel_id=channel_id)


def _start_signal_reaction_listener(bot: commands.Bot) -> None:
    from src.bot.signal_reaction_listener import SignalReactionListener

    listener = SignalReactionListener(bot)
    listener.register()


def _start_invalidation_subscriber(bot: commands.Bot) -> None:
    from src.bot.invalidation_subscriber import InvalidationSubscriber

    channel_id = getattr(settings, "alert_channel_id", None)
    if not channel_id:
        logger.warning(
            "bot.invalidation_subscriber.not_available",
            reason="alert_channel_id not configured — thesis invalidation alerts disabled",
        )
        return

    subscriber = InvalidationSubscriber(channel_id=int(channel_id))
    subscriber.set_client(bot)
    subscriber.register()
    logger.info("bot.invalidation_subscriber.registered", channel_id=channel_id)


def _start_position_risk_subscriber(bot: commands.Bot) -> None:
    """Wire position loss threshold breach → Discord embed.

    Delivery chain:
        PnlService._calc_position_pnl()          [portfolio segment]
          → _maybe_emit_risk_breach()
            → PositionRiskBreachedEvent           [platform/event_bus]
              → PositionRiskSubscriber._handle()  [bot — Discord alert]
                → embed: {emoji} Position risk — {SYMBOL}
                → channel.send(embed)             [alert_channel]

    Thresholds:
        CRITICAL: unrealized_pct <= -15%  → red, urgency CRITICAL
        WARN:     unrealized_pct <= -8%   → orange, urgency TODAY

    Dedup: dedup_key per user+symbol+breach_type, window=6h.
    """
    from src.bot.position_risk_subscriber import PositionRiskSubscriber

    channel_id = getattr(settings, "alert_channel_id", None)
    if not channel_id:
        logger.warning(
            "bot.position_risk_subscriber.not_available",
            reason="alert_channel_id not configured — position risk alerts disabled",
        )
        return

    subscriber = PositionRiskSubscriber(channel_id=int(channel_id))
    subscriber.set_client(bot)
    subscriber.register()
    logger.info("bot.position_risk_subscriber.registered", channel_id=channel_id)


def _start_portfolio_snapshot_scheduler(bot: commands.Bot) -> None:
    """Start PortfolioSnapshotScheduler — 08:15 ICT weekdays.

    Emits PortfolioSnapshotRequestedEvent so portfolio/snapshot_listener.py
    builds PortfolioSnapshotReadyEvent before IntelligenceEngine (08:35 ICT).
    """
    from src.bot.scheduler import PortfolioSnapshotScheduler

    scheduler = PortfolioSnapshotScheduler(bot)
    scheduler.start()
    logger.info("bot.portfolio_snapshot_scheduler.started")


def _start_investor_profile_scheduler(bot: commands.Bot) -> None:
    """Start InvestorProfileScheduler — 08:20 ICT weekdays.

    Snapshots the investor profile before ThesisMaintenance (08:30) and
    BriefingScheduler (08:30) so morning brief has fresh profile context.
    """
    from src.bot.scheduler import InvestorProfileScheduler

    scheduler = InvestorProfileScheduler(bot)
    scheduler.start()
    logger.info("bot.investor_profile_scheduler.started")


def _wire_feedback_loop_monitor(bot: commands.Bot) -> None:
    """Wire FeedbackLoopMonitor alert channel after bot login.

    Reuses DISCORD_ALERT_CHANNEL_ID — same channel as SchedulerMonitor.
    Must be called last (after all subscribers registered) so the monitor
    can start receiving record_ok / record_error calls immediately.
    """
    from src.platform.feedback_loop_monitor import get_feedback_monitor

    channel_id = getattr(settings, "alert_channel_id", None)
    if not channel_id:
        logger.warning(
            "bot.feedback_loop_monitor.not_available",
            reason="alert_channel_id not configured — feedback error-rate alerts disabled",
        )
        return

    channel = bot.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "bot.feedback_loop_monitor.channel_not_found",
            channel_id=channel_id,
            channel_type=type(channel).__name__ if channel is not None else None,
        )
        return

    get_feedback_monitor().set_alert_channel(channel)
    logger.info("bot.feedback_loop_monitor.wired", channel_id=channel_id)


def _start_eod_close_capture_scheduler(bot: commands.Bot) -> None:
    """Start EodCloseCaptureScheduler — 14:50 + 15:05 ICT ngày giao dịch (Wave C1).

    Chụp giá khớp ATC cho vị thế + watchlist + thesis active, gắn Quote.close và
    persist market_quote_cache để EOD snapshot / outcome filler / dashboard dùng
    đúng giá đóng cửa thay vì giá fetch cuối trong phiên.
    """
    from src.bot.scheduler import EodCloseCaptureScheduler

    EodCloseCaptureScheduler(bot).start()
    logger.info("bot.eod_close_capture_scheduler.started")


def _start_eod_portfolio_snapshot_scheduler(bot: commands.Bot) -> None:
    """Start EodPortfolioSnapshotScheduler — 15:20 ICT weekdays.

    Writes persistent EOD P&L snapshot per position to position_daily_snapshots.
    Dashboard reads from snapshot as primary source — independent of QuoteService.
    Catch-up: runs immediately on start if today's snapshot is missing (after 15:20 ICT).
    """
    from src.bot.scheduler import EodPortfolioSnapshotScheduler

    scheduler = EodPortfolioSnapshotScheduler(bot)
    scheduler.start()
    logger.info("bot.eod_portfolio_snapshot_scheduler.started")


def _start_deriv_sim_scheduler(bot: commands.Bot) -> None:
    """Start DerivSimScheduler — giả lập HĐTL VN30 theo base rate, quét 5 phút."""
    from src.bot.scheduler import DerivSimScheduler

    DerivSimScheduler(bot).start()
    logger.info("bot.deriv_sim_scheduler.started")


def _start_plan_check_scheduler(bot: commands.Bot) -> None:
    """Start PlanCheckScheduler — kiểm tra kế hoạch nắm giữ 15:30 ICT ngày giao dịch."""
    from src.bot.scheduler import PlanCheckScheduler

    PlanCheckScheduler(bot).start()
    logger.info("bot.plan_check_scheduler.started")
