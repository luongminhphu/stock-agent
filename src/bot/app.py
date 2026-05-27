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
    intents.reactions = True  # Wave B: required for on_raw_reaction_add

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
            _inject_intelligence_listener(bot)
            await _register_cogs(bot)
            await _sync_tree(bot)

            _start_briefing_scheduler(bot)
            _start_scan_scheduler(bot)
            _start_thesis_maintenance_scheduler(bot)
            _start_drift_scheduler(bot)
            _start_snapshot_scheduler()
            _start_reminder_scheduler(bot)
            _start_outcome_filler_scheduler(bot)
            _start_decision_replay_scheduler(bot)
            _start_memory_consolidator_scheduler(bot)
            _start_recommendation_listener(bot)
            _start_opportunity_screen_scheduler(bot)
            _start_proactive_watch_scheduler(bot)
            _start_proactive_watch_subscriber(bot)
            _start_post_mortem_subscriber(bot)          # Wave E: thesis post-mortem → Discord
            _start_intelligence_engine_scheduler(bot)   # Wave A: IE verdict → Discord (2×/day)
            _start_signal_reaction_listener(bot)        # Wave B: emoji reaction → user_signal
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
    from src.bot.commands.debate import DebateCog
    from src.bot.commands.decision import DecisionCog
    from src.bot.commands.health import HealthCog
    from src.bot.commands.help import HelpCog
    from src.bot.commands.market import MarketCog
    from src.bot.commands.memory import MemoryCog
    from src.bot.commands.portfolio import PortfolioCog
    from src.bot.commands.pretrade import PretradeCog
    from src.bot.commands.reviews import ReviewsCog
    from src.bot.commands.scheduler_trigger import SchedulerTriggerCog
    from src.bot.commands.sector_rotation import SectorRotationCog
    from src.bot.commands.stress_test import StressTestCog
    from src.bot.commands.thesis_crud import ThesisCrudCog
    from src.bot.commands.thesis_review import ThesisReviewCog
    from src.bot.commands.trend import TrendCog
    from src.bot.commands.watchlist import WatchlistCog
    from src.bot.commands.why import WhyCog

    await bot.add_cog(WatchlistCog(bot))
    await bot.add_cog(ThesisCrudCog(bot))
    await bot.add_cog(ThesisReviewCog(bot))
    await bot.add_cog(DebateCog(bot))
    await bot.add_cog(MarketCog(bot))
    await bot.add_cog(BriefingCog(bot))
    await bot.add_cog(HelpCog(bot))
    await bot.add_cog(WhyCog(bot))
    await bot.add_cog(PretradeCog(bot))
    await bot.add_cog(ConvictionTimelineCog(bot))
    await bot.add_cog(PortfolioCog(bot))
    await bot.add_cog(StressTestCog(bot))
    await bot.add_cog(DecisionCog(bot))
    await bot.add_cog(SchedulerTriggerCog(bot))
    await bot.add_cog(HealthCog(bot))
    await bot.add_cog(SectorRotationCog(bot))
    await bot.add_cog(MemoryCog(bot))
    await bot.add_cog(TrendCog(bot))
    await bot.add_cog(ReviewsCog(bot))
    logger.info(
        "bot.cogs_loaded",
        cogs=[
            "WatchlistCog", "ThesisCrudCog", "ThesisReviewCog", "DebateCog",
            "MarketCog", "BriefingCog", "HelpCog", "WhyCog", "PretradeCog",
            "ConvictionTimelineCog", "PortfolioCog", "StressTestCog",
            "DecisionCog", "SchedulerTriggerCog", "HealthCog", "SectorRotationCog",
            "MemoryCog", "TrendCog", "ReviewsCog",
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


def _inject_intelligence_listener(bot: commands.Bot) -> None:
    """Inject Discord client into IntelligenceEngineListener for verdict pushes."""
    from src.platform.bootstrap import get_intelligence_engine_listener
    listener = get_intelligence_engine_listener()
    if listener is not None:
        listener.set_client(bot)
        logger.info("bot.intelligence_listener.client_injected")
    else:
        logger.warning(
            "bot.intelligence_listener.not_available",
            reason="IntelligenceEngineListener not initialised at bootstrap — verdict Discord push disabled.",
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


def _start_drift_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ThesisDriftScheduler
    scheduler = ThesisDriftScheduler(bot)
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
    from src.platform.bootstrap import get_memory_consolidator
    from src.bot.scheduler import MemoryConsolidatorScheduler

    if get_memory_consolidator() is None:
        logger.warning(
            "bot.memory_consolidator_scheduler.not_available",
            reason="MemoryConsolidator not initialised",
        )
        return

    scheduler = MemoryConsolidatorScheduler(bot)
    scheduler.start()


def _start_recommendation_listener(bot: commands.Bot) -> None:
    from src.bot.recommendation_listener import RecommendationListener
    listener = RecommendationListener(bot)
    listener.register()


def _start_opportunity_screen_scheduler(bot: commands.Bot) -> None:
    from src.platform.bootstrap import (
        get_opportunity_screen_scheduler,
        get_opportunity_screen_subscriber,
    )

    scheduler = get_opportunity_screen_scheduler()
    if scheduler is None:
        logger.warning(
            "bot.opportunity_screen_scheduler.not_available",
            reason="bootstrap did not initialise OpportunityScreenScheduler",
        )
        return

    scheduler.start()
    logger.info("bot.opportunity_screen_scheduler.started")

    subscriber = get_opportunity_screen_subscriber()
    if subscriber is not None and hasattr(subscriber, "set_client"):
        subscriber.set_client(bot)
        logger.info("bot.opportunity_screen_subscriber.client_injected")


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
    """Wire Wave E: PostMortemSubscriber → Discord embed on thesis close/invalidate."""
    from src.bot.post_mortem_subscriber import PostMortemSubscriber

    channel_id = (
        getattr(settings, "decision_channel_id", None)
        or getattr(settings, "morning_channel_id", None)
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


def _start_intelligence_engine_scheduler(bot: commands.Bot) -> None:
    """Wave A: start IntelligenceEngineScheduler — fires engine 2x/day.

    Timing:
        morning — 08:35 ICT  (after ThesisMaintenance 08:30, before SignalEngine 08:40)
        eod     — 15:12 ICT  (after SignalEngine.eod 15:10, before DecisionReplay 15:15)

    Delivery chain (no Discord logic here):
        IntelligenceEngineScheduler._run_cycle()
          → engine.run_cycle()                          [core]
            → IntelligenceEngineCompletedEvent          [platform/event_bus]
              → IntelligenceEngineListener._push_discord()  [core, client injected above]
                → build_engine_verdict_embed()          [bot/discord_helper]
                → safe_send(alert_channel)              [bot/discord_helper]
    """
    from src.bot.scheduler import IntelligenceEngineScheduler
    scheduler = IntelligenceEngineScheduler(bot)
    scheduler.start()
    logger.info("bot.intelligence_engine_scheduler.started")


def _start_signal_reaction_listener(bot: commands.Bot) -> None:
    """Wire Wave B: SignalReactionListener → captures emoji reactions as investor signals.

    Requires intents.reactions = True (set in create_bot()).
    Always registers — no config dependency.
    """
    from src.bot.signal_reaction_listener import SignalReactionListener
    listener = SignalReactionListener(bot)
    listener.register()
