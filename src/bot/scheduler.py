"""Scheduler — orchestrate recurring bot tasks.

Owner: bot segment (adapter only).
No business logic — calls domain services on schedule.

Registered tasks:
    InvestorProfileScheduler.snapshot_task     — weekdays 08:20 ICT (before maintenance + brief)
    BriefingScheduler.morning_brief_task       — weekdays 08:30 ICT
    BriefingScheduler.eod_brief_task           — weekdays 15:00 ICT
    WatchlistScanScheduler.scan_task           — every 5 min, weekdays 09:00–15:00 ICT
    ThesisMaintenanceScheduler.maintenance     — weekdays 08:30 ICT (before morning brief)
    ThesisDriftScheduler.drift_task            — every 15 min, weekdays 09:00–15:00 ICT
    ReminderScheduler.daily_task               — weekdays 08:00 ICT (DAILY reminders)
    ReminderScheduler.weekly_task              — Mondays 08:00 ICT (WEEKLY reminders)
    OutcomeFillerScheduler.fill_task           — weekdays 15:05 ICT (fill decision outcomes before replay)
    DecisionReplayScheduler.replay_task        — weekdays 15:15 ICT (after market close)
    MemoryConsolidatorScheduler.consolidate    — Sundays 09:00 ICT (weekly memory distill)
    SignalEngineScheduler.morning_task         — weekdays 08:40 ICT (before morning brief)
    SignalEngineScheduler.eod_task             — weekdays 15:10 ICT (after market close)
    AgendaBuilderScheduler.agenda_task         — weekdays 07:30 ICT (before all morning tasks)
    ProactiveWatchScheduler.morning_task       — weekdays 09:15 ICT (after market open)
    ProactiveWatchScheduler.midday_task        — weekdays 11:15 ICT (mid-session)
    ProactiveWatchScheduler.pre_atc_task       — weekdays 14:15 ICT (before ATC)
    PortfolioSnapshotScheduler.morning_task    — weekdays 08:15 ICT (before IntelligenceEngine 08:35)
    IntelligenceEngineScheduler.morning_task   — weekdays 08:35 ICT (after ThesisMaintenance, before SignalEngine)
    IntelligenceEngineScheduler.eod_task       — weekdays 15:12 ICT (after SignalEngine.eod, before DecisionReplay)

Note:
    MORNING_CHANNEL_ID and EOD_CHANNEL_ID must be set in settings.
    SCHEDULER_USER_ID is the service account used for scheduled tasks.

Channel routing:
    Briefing, ThesisMaintenance, InvestorProfile, DecisionReplay → morning_channel_id
    Proactive alerts (WatchlistScan, ThesisDrift, Reminder, ProactiveWatch)
                                                                 → alert_channel_id
        alert_channel_id = DISCORD_ALERT_CHANNEL_ID if set, else morning_channel_id
    SignalEngineScheduler emits events only — no channel routing, no Discord message.
    AgendaBuilderScheduler persists only — no Discord message.
    OutcomeFillerScheduler persists only — no Discord message (silent data enrichment job).
    ProactiveWatchScheduler emits events only — Discord delivery via ProactiveWatchSubscriber.
    IntelligenceEngineScheduler emits events only — no channel routing, no Discord message.
        Wave 2: IntelligenceEngineListener (core segment) handles run_cycle + verdict + delivery.
    PortfolioSnapshotScheduler emits events only — no channel routing, no Discord message.
        portfolio.PortfolioSnapshotListener handles P&L build + PortfolioSnapshotReadyEvent.

Wave 8:
    BriefingScheduler no longer calls BriefingService directly.
    It emits BriefingRequestedEvent → BriefingListener (briefing segment)
    handles delivery. Bot is a thin timing adapter only.

Wave 2 (Signal Engine):
    SignalEngineScheduler emits SignalEngineRequestedEvent → ai.SignalEngineListener
    runs watchlist × thesis × portfolio cross-check → emits SignalEngineCompletedEvent
    → briefing.BriefingListener injects summary into brief context.

Wave D (ProactiveWatch):
    ProactiveWatchScheduler emits ProactiveWatchRequestedEvent (3×/day)
    → watchlist.ProactiveWatchListener runs ScanService + AlertService
    → ProactiveWatchAlertFiredEvent × N
    → bot.ProactiveWatchSubscriber batches and sends Discord embed.

Core Engine (Wave 1):
    IntelligenceEngineScheduler emits IntelligenceEngineRequestedEvent (2×/day)
    → core.IntelligenceEngineListener.run_cycle() builds SystemSnapshot
    → publishes IntelligenceEngineCompletedEvent
    → bot subscriber → Discord.

Portfolio Snapshot loop:
    PortfolioSnapshotScheduler emits PortfolioSnapshotRequestedEvent (08:15 ICT)
    → portfolio.PortfolioSnapshotListener calls PnlService.get_portfolio_pnl()
    → publishes PortfolioSnapshotReadyEvent (enriched: unrealized_pnl_pct, top_exposed_tickers)
    → core.IntelligenceEngineListener caches snapshot per user_id
    → IntelligenceEngineScheduler fires 08:35 ICT (20-min window)
    → portfolio context injected into engine.run_cycle() via context_hint.

Wave E (Thesis Score Sensitivity):
    WatchlistScanScheduler injects ThesisScoreQuery(session) into ScanService.
    Tickers with thesis health score < 50 (Weak/Critical) fire on 2% moves
    instead of the default 3% threshold — earlier signal when thesis is struggling.
"""

from __future__ import annotations

import datetime

import discord
from discord.ext import tasks

from src.bot.commands.decision_embeds import build_replay_embed
from src.bot.commands.reminder_embeds import build_reminder_embed
from src.bot.commands.thesis_embeds import build_drift_embed, build_maintenance_embed
from src.bot.commands.watchlist_embeds import build_scan_embed
from src.platform.config import settings
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.platform.scheduler_monitor import SchedulerMonitor, get_monitor

# NOTE: src.platform.bootstrap imports are intentionally lazy (inline inside each
# method that uses them) to break the circular import:
#   bootstrap → src.bot.intelligence_engine_subscriber
#   → bot/__init__.py → scheduler.py (module-level)
#   → bootstrap (mid-load) → ImportError
# All other domain-service imports in this file follow the same lazy pattern.

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared market-hours bounds (UTC)
# Used by WatchlistScanScheduler and ThesisDriftScheduler.
# Both comparisons use naive time — strip tzinfo before comparing.
# ---------------------------------------------------------------------------

_MARKET_OPEN_UTC  = datetime.time(hour=2, minute=0)   # 09:00 ICT
_MARKET_CLOSE_UTC = datetime.time(hour=8, minute=0)   # 15:00 ICT


def _in_market_hours(now_utc: datetime.datetime) -> bool:
    """Return True if now_utc falls within Vietnamese market hours (Mon–Fri)."""
    if now_utc.weekday() >= 5:
        return False
    now_naive = now_utc.utctimetuple()
    now_time = datetime.time(hour=now_naive.tm_hour, minute=now_naive.tm_min)
    return _MARKET_OPEN_UTC <= now_time <= _MARKET_CLOSE_UTC


# ---------------------------------------------------------------------------
# InvestorProfileScheduler
# ---------------------------------------------------------------------------

_INVESTOR_PROFILE_TIME = datetime.time(hour=1, minute=20, tzinfo=datetime.UTC)  # 08:20 ICT


class InvestorProfileScheduler:
    """Build daily InvestorProfileSnapshot at 08:20 ICT (weekdays).

    Runs 10 min before ThesisMaintenanceScheduler (08:30) and
    10 min before BriefingScheduler (08:30) so the morning brief
    always has fresh behavioral patterns, win_rate, and lessons.

    Flow:
        1. get_investor_profile_service() — retrieve (class, user_id) from bootstrap.
        2. InvestorProfileService.build_snapshot(user_id) — pure data aggregation, no AI.
        3. session.commit() — persist snapshot.
        4. Log result; record success/failure in SchedulerMonitor.

    Graceful skip:
        - If get_investor_profile_service() returns None (scheduler_user_id not set).
        - All exceptions are caught and recorded; never blocks downstream schedulers.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("investor_profile.snapshot")
        self._snapshot_task.start()
        logger.info("scheduler.investor_profile.started", time_ict="08:20")

    def stop(self) -> None:
        self._snapshot_task.cancel()
        logger.info("scheduler.investor_profile.stopped")

    @tasks.loop(time=_INVESTOR_PROFILE_TIME)
    async def _snapshot_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:
            return

        task_name = "investor_profile.snapshot"

        from src.platform.bootstrap import get_investor_profile_service
        result = get_investor_profile_service()
        if result is None:
            logger.warning(
                "scheduler.investor_profile.skipped",
                reason="investor_profile_service not initialised — scheduler_user_id not set",
            )
            return

        svc_class, user_id = result

        try:
            async with AsyncSessionLocal() as session:
                svc = svc_class(session)
                snapshot = await svc.build_snapshot(user_id=user_id)
                await session.commit()

            logger.info(
                "scheduler.investor_profile.snapshot_built",
                user_id=user_id,
                active_thesis_count=snapshot.active_thesis_count,
                win_rate_30d=snapshot.win_rate_30d,
                avg_hold_days=snapshot.avg_hold_days,
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.investor_profile.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_snapshot_task.before_loop
    async def _before_snapshot(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# BriefingScheduler
# ---------------------------------------------------------------------------

_MORNING_TIME = datetime.time(hour=1, minute=30, tzinfo=datetime.UTC)  # 08:30 ICT
_EOD_TIME     = datetime.time(hour=8, minute=0,  tzinfo=datetime.UTC)  # 15:00 ICT


class BriefingScheduler:
    """Emit BriefingRequestedEvent on schedule. Attach to a discord.Client after login.

    Wave 8: scheduler is now a thin timing adapter only.
    All briefing domain logic (BriefingService, Discord delivery) lives in
    BriefingListener (briefing segment). Bot emits the event; briefing handles the rest.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("briefing.morning")
        self._monitor.register_task("briefing.eod")
        self._morning_task.start()
        self._eod_task.start()
        logger.info("scheduler.briefing.started")

    def stop(self) -> None:
        self._morning_task.cancel()
        self._eod_task.cancel()
        logger.info("scheduler.briefing.stopped")

    @tasks.loop(time=_MORNING_TIME)
    async def _morning_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit_brief(phase="morning")

    @_morning_task.before_loop
    async def _before_morning(self) -> None:
        await self._client.wait_until_ready()

    @tasks.loop(time=_EOD_TIME)
    async def _eod_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit_brief(phase="eod")

    @_eod_task.before_loop
    async def _before_eod(self) -> None:
        await self._client.wait_until_ready()

    async def _emit_brief(self, phase: str) -> None:
        """Emit BriefingRequestedEvent — bot's only responsibility for briefing.

        BriefingListener (briefing segment) handles the rest:
        BriefingService call, embed build, Discord delivery, BriefingReadyEvent emit.
        """
        from src.platform.event_bus import get_event_bus
        from src.platform.events import BriefingRequestedEvent

        task_name = f"briefing.{phase}"
        user_id = getattr(settings, "scheduler_user_id", None)

        if not user_id:
            logger.warning(
                "scheduler.briefing.skipped",
                phase=phase,
                reason="scheduler_user_id not configured",
            )
            return

        try:
            bus = get_event_bus()
            await bus.publish(
                BriefingRequestedEvent(
                    brief_type=phase,
                    triggered_by="scheduler",
                )
            )
            logger.info("scheduler.briefing.event_emitted", phase=phase)
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.briefing.emit_error", phase=phase, error=str(exc))
            await self._monitor.record_failure(task_name, exc)


# ---------------------------------------------------------------------------
# WatchlistScanScheduler
# ---------------------------------------------------------------------------

_SCAN_INTERVAL_MINUTES = 5


class WatchlistScanScheduler:
    """Scan watchlist every 5 minutes during market hours and notify Discord.

    - Runs weekdays 09:00–15:00 ICT only (silent outside market hours).
    - Step 0: reactivate_cooled_down() — re-arm auto_reactivate alerts past
      their cooldown window (isolated session, non-blocking).
    - Sends embed only when signals exist (alert_triggered or strong_move).
    - ON_SIGNAL reminders piggyback on the same embed — no extra message.
    - Does NOT call AI — zero token cost.
    - Channel: settings.alert_channel_id (DISCORD_ALERT_CHANNEL_ID → morning_channel_id).

    Wave 2 enrichment:
    - TickerDirectionQuery injected into ScanService to enable THESIS_DIVERGENCE signals.
      Created per-tick with the same session as ScanService (session-scoped, not singleton).

    Wave E enrichment:
    - ThesisScoreQuery injected into ScanService to lower the strong_move threshold
      from 3% → 2% for tickers whose thesis health score is Weak/Critical (< 50).
      Created per-tick alongside TickerDirectionQuery — same session, zero extra cost.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("watchlist.scan")
        self._scan_task.start()
        logger.info("scheduler.scan.started", interval_minutes=_SCAN_INTERVAL_MINUTES)

    def stop(self) -> None:
        self._scan_task.cancel()
        logger.info("scheduler.scan.stopped")

    @tasks.loop(minutes=_SCAN_INTERVAL_MINUTES)
    async def _scan_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if not _in_market_hours(now_utc):
            return

        task_name = "watchlist.scan"
        user_id = getattr(settings, "scheduler_user_id", None)
        channel_id = settings.alert_channel_id or None
        if not user_id or not channel_id:
            logger.warning(
                "scheduler.scan.skipped",
                reason="scheduler_user_id or alert_channel_id not configured",
            )
            return

        # ── Step 0: Reactivate cooled-down alerts (isolated commit) ─────────────────────────────────────────────
        try:
            from src.watchlist.alert_service import AlertService

            async with AsyncSessionLocal() as reactivate_session:
                alert_svc = AlertService(reactivate_session)
                reactivated = await alert_svc.reactivate_cooled_down(
                    str(user_id),
                    cooldown_hours=settings.alert_reactivate_cooldown_hours,
                )
                await reactivate_session.commit()
            if reactivated:
                logger.info(
                    "scheduler.scan.alerts_reactivated",
                    count=len(reactivated),
                    tickers=[a.ticker for a in reactivated],
                )
        except Exception as exc:
            logger.warning("scheduler.scan.reactivate_failed", error=str(exc))

        # ── Step 1: Scan ─────────────────────────────────────────────────────────────────────────────────
        try:
            from src.platform.bootstrap import get_quote_service
            from src.thesis.ticker_direction_query import TickerDirectionQuery
            from src.watchlist.scan_service import ScanService
            from src.watchlist.thesis_score_query import ThesisScoreQuery

            async with AsyncSessionLocal() as session:
                svc = ScanService(
                    session=session,
                    quote_service=get_quote_service(),
                    ticker_direction_query=TickerDirectionQuery(session),
                    thesis_score_query=ThesisScoreQuery(session),
                )
                result = await svc.scan_user(str(user_id))
                await session.commit()

            if not result.signals and not result.on_signal_reminders:
                await self._monitor.record_success(task_name)
                return

            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning("scheduler.scan.channel_not_found", channel_id=channel_id)
                await self._monitor.record_failure(
                    task_name, RuntimeError(f"channel {channel_id} not found")
                )
                return

            embed = build_scan_embed(result, now_utc)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.scan.notified",
                signals=len(result.signals),
                triggered=result.triggered_count,
                on_signal_reminders=len(result.on_signal_reminders),
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.scan.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_scan_task.before_loop
    async def _before_scan(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# ThesisMaintenanceScheduler
# ---------------------------------------------------------------------------

_MAINTENANCE_TIME        = datetime.time(hour=1, minute=30, tzinfo=datetime.UTC)  # 08:30 ICT
_MAINTENANCE_STALE_DAYS  = 3
_CATALYST_LOOKAHEAD_DAYS = 30  # fetch catalysts within this window


class ThesisMaintenanceScheduler:
    """Chạy lúc 08:30 ICT mỗi ngày làm việc — cùng slot với morning brief.

    Flow:
        1.  auto_expire_overdue_catalysts()  — không tốn token, chạy đầu tiên.
                                               Fail → log + record_failure, tiếp tục (non-blocking).
        1b. get_upcoming_catalysts()         — lấy catalyst sắp đến trong 30 ngày.
                                               Isolated session, non-blocking.
                                               Chạy độc lập với Step 1 — Step 1 fail không ngăn Step 1b.
        2.  review_stale_theses()            — AI review, chỉ khi thesis stale > 3 ngày.
        3.  Discord notify nếu có thay đổi hoặc có upcoming catalysts chưa notified hôm nay.

    Tất cả các bước dùng session riêng biệt — expire, upcoming query và review độc lập nhau.
    Bước nào fail chỉ ảnh hưởng đến output của chính nó, không rollback bước khác.

    Deduplication (in-memory, no DB migration):
        _notified_catalyst_ids — set[int] of catalyst IDs already notified today.
        _last_dedup_date       — resets the set when the calendar date changes.
        Upcoming catalysts are filtered to new-only before building the embed.
        IDs are added to the set only after a successful Discord send.
        The set resets naturally on process restart (daily cron runs once/day).
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()
        # Dedup state — in-memory, resets on new calendar day or process restart
        self._notified_catalyst_ids: set[int] = set()
        self._last_dedup_date: datetime.date | None = None

    def _reset_dedup_if_new_day(self, today: datetime.date) -> None:
        """Reset the notified-IDs set when the calendar date has changed."""
        if self._last_dedup_date != today:
            if self._notified_catalyst_ids:
                logger.info(
                    "scheduler.thesis_maintenance.dedup_reset",
                    previous_date=str(self._last_dedup_date),
                    cleared_ids=len(self._notified_catalyst_ids),
                )
            self._notified_catalyst_ids = set()
            self._last_dedup_date = today

    def start(self) -> None:
        self._monitor.register_task("thesis.maintenance")
        self._maintenance_task.start()
        logger.info("scheduler.thesis_maintenance.started")

    def stop(self) -> None:
        self._maintenance_task.cancel()
        logger.info("scheduler.thesis_maintenance.stopped")

    @tasks.loop(time=_MAINTENANCE_TIME)
    async def _maintenance_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:
            return

        task_name = "thesis.maintenance"
        user_id = getattr(settings, "scheduler_user_id", None)
        channel_id = getattr(settings, "morning_channel_id", None)
        if not user_id:
            logger.warning(
                "scheduler.thesis_maintenance.skipped",
                reason="scheduler_user_id not configured",
            )
            return

        today = now_utc.date()
        self._reset_dedup_if_new_day(today)

        expired_count = 0
        reviews: list = []
        upcoming_catalysts: list[dict] = []

        # -- Step 1: Auto-expire overdue catalysts (no AI, no token cost) --
        try:
            from src.thesis.component_service import ComponentService

            async with AsyncSessionLocal() as session:
                svc = ComponentService(session)
                expired_count = await svc.auto_expire_overdue_catalysts(str(user_id))
                await session.commit()
            logger.info(
                "scheduler.thesis_maintenance.expire_done",
                expired_count=expired_count,
            )
        except Exception as exc:
            logger.error("scheduler.thesis_maintenance.expire_error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

        # -- Step 1b: Fetch upcoming catalysts (no AI, isolated session, non-blocking) --
        try:
            from src.readmodel.thesis_query_service import ThesisQueryService

            async with AsyncSessionLocal() as session:
                query_svc = ThesisQueryService(session)
                all_upcoming = await query_svc.get_upcoming_catalysts(
                    str(user_id), days=_CATALYST_LOOKAHEAD_DAYS
                )

            new_upcoming = [
                c for c in all_upcoming
                if c.get("id") not in self._notified_catalyst_ids
            ]

            logger.info(
                "scheduler.thesis_maintenance.upcoming_catalysts_fetched",
                total=len(all_upcoming),
                new=len(new_upcoming),
                already_notified=len(all_upcoming) - len(new_upcoming),
            )
            upcoming_catalysts = new_upcoming
        except Exception as exc:
            logger.warning(
                "scheduler.thesis_maintenance.upcoming_catalysts_error",
                error=str(exc),
            )
            upcoming_catalysts = []

        # -- Step 2: AI review for stale theses --
        try:
            from src.platform.bootstrap import get_quote_service, get_thesis_review_agent
            from src.thesis.review_service import ReviewService

            async with AsyncSessionLocal() as session:
                svc = ReviewService(
                    session=session,
                    agent=get_thesis_review_agent(),  # type: ignore[arg-type]
                    quote_service=get_quote_service(),
                )
                reviews = await svc.review_stale_theses(
                    user_id=str(user_id),
                    stale_days=_MAINTENANCE_STALE_DAYS,
                )
                await session.commit()
            logger.info(
                "scheduler.thesis_maintenance.review_done",
                reviewed_count=len(reviews),
            )
        except Exception as exc:
            logger.error("scheduler.thesis_maintenance.review_error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)
            return

        await self._monitor.record_success(task_name)

        has_content = expired_count > 0 or reviews or upcoming_catalysts
        if not has_content:
            return

        if not channel_id:
            logger.warning(
                "scheduler.thesis_maintenance.notify_skipped",
                reason="morning_channel_id not configured",
                expired_count=expired_count,
                review_count=len(reviews),
                upcoming_count=len(upcoming_catalysts),
            )
            return

        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            logger.warning(
                "scheduler.thesis_maintenance.channel_not_found",
                channel_id=channel_id,
            )
            return

        try:
            embed = build_maintenance_embed(
                expired_count=expired_count,
                reviews=reviews,
                now_utc=now_utc,
                upcoming_catalysts=upcoming_catalysts if upcoming_catalysts else None,
            )
            await channel.send(embed=embed)  # type: ignore[union-attr]

            newly_notified_ids = {
                c["id"] for c in upcoming_catalysts if c.get("id") is not None
            }
            self._notified_catalyst_ids.update(newly_notified_ids)

            logger.info(
                "scheduler.thesis_maintenance.notified",
                upcoming_catalyst_count=len(upcoming_catalysts),
                dedup_set_size=len(self._notified_catalyst_ids),
            )
        except Exception as exc:
            logger.error("scheduler.thesis_maintenance.notify_error", error=str(exc))

    @_maintenance_task.before_loop
    async def _before_maintenance(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# ThesisDriftScheduler
# ---------------------------------------------------------------------------

_DRIFT_INTERVAL_MINUTES = 15


class ThesisDriftScheduler:
    """Detect thesis price drift + conviction drift every 15 min during market hours.

    Flow (per tick):
        1a. DriftService.detect()              — price drift, pure detection, no AI.
        1b. ConvictionDriftDetector.detect_all() — conviction decay, pure detection, no AI.
        2.  For each price DriftSignal: ReviewService.review_thesis() — AI review.
            Conviction-only signals: notify without AI review (wave 1 scope).
        3.  Discord notify with combined price + conviction drift embed.

    Cooldown is enforced inside DriftService and ConvictionDriftDetector (default 4h).

    Channel: settings.alert_channel_id (DISCORD_ALERT_CHANNEL_ID → morning_channel_id).
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("thesis.drift")
        self._drift_task.start()
        logger.info(
            "scheduler.drift.started",
            interval_minutes=_DRIFT_INTERVAL_MINUTES,
            threshold_pct=settings.thesis_drift_threshold_pct,
            cooldown_hours=settings.thesis_drift_cooldown_hours,
        )

    def stop(self) -> None:
        self._drift_task.cancel()
        logger.info("scheduler.drift.stopped")

    @tasks.loop(minutes=_DRIFT_INTERVAL_MINUTES)
    async def _drift_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if not _in_market_hours(now_utc):
            return

        task_name = "thesis.drift"
        user_id = getattr(settings, "scheduler_user_id", None)
        channel_id = settings.alert_channel_id or None
        if not user_id or not channel_id:
            logger.warning(
                "scheduler.drift.skipped",
                reason="scheduler_user_id or alert_channel_id not configured",
            )
            return

        try:
            from src.platform.bootstrap import get_quote_service, get_thesis_review_agent
            from src.thesis.conviction_drift_detector import ConvictionDriftDetector
            from src.thesis.drift_service import DriftService
            from src.thesis.review_service import ReviewService

            async with AsyncSessionLocal() as session:
                drift_svc = DriftService(
                    session=session,
                    quote_service=get_quote_service(),
                    threshold_pct=settings.thesis_drift_threshold_pct,
                    cooldown_hours=settings.thesis_drift_cooldown_hours,
                )
                signals = await drift_svc.detect(str(user_id))

            conviction_signals = []
            try:
                async with AsyncSessionLocal() as session:
                    conviction_detector = ConvictionDriftDetector(
                        session=session,
                        cooldown_hours=settings.thesis_drift_cooldown_hours,
                    )
                    conviction_signals = await conviction_detector.detect_all(str(user_id))

                if conviction_signals:
                    logger.info(
                        "scheduler.drift.conviction_signals_found",
                        count=len(conviction_signals),
                        tickers=[s.ticker for s in conviction_signals],
                    )
            except Exception as exc:
                logger.warning("scheduler.drift.conviction_detect_failed", error=str(exc))

            if not signals and not conviction_signals:
                await self._monitor.record_success(task_name)
                return

            reviewed_signals = []
            for signal in signals:
                try:
                    async with AsyncSessionLocal() as session:
                        review_svc = ReviewService(
                            session=session,
                            agent=get_thesis_review_agent(),  # type: ignore[arg-type]
                            quote_service=get_quote_service(),
                        )
                        review = await review_svc.review_thesis(
                            user_id=str(user_id),
                            thesis_id=signal.thesis_id,
                        )
                        await session.commit()
                    reviewed_signals.append((signal, review))
                except Exception as exc:
                    logger.warning(
                        "scheduler.drift.review_failed",
                        thesis_id=signal.thesis_id,
                        error=str(exc),
                    )
                    reviewed_signals.append((signal, None))

            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning("scheduler.drift.channel_not_found", channel_id=channel_id)
                await self._monitor.record_failure(
                    task_name, RuntimeError(f"channel {channel_id} not found")
                )
                return

            embed = build_drift_embed(
                reviewed_signals=reviewed_signals,
                conviction_signals=conviction_signals,
                now_utc=now_utc,
            )
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.drift.notified",
                price_signals=len(reviewed_signals),
                conviction_signals=len(conviction_signals),
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.drift.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_drift_task.before_loop
    async def _before_drift(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# ReminderScheduler
# ---------------------------------------------------------------------------

_REMINDER_DAILY_TIME  = datetime.time(hour=1, minute=0, tzinfo=datetime.UTC)  # 08:00 ICT
_REMINDER_WEEKLY_TIME = datetime.time(hour=1, minute=0, tzinfo=datetime.UTC)  # 08:00 ICT Mon


class ReminderScheduler:
    """Send scheduled reminders to Discord at 08:00 ICT.

    - daily_task  — runs every weekday, sends DAILY reminders due today.
    - weekly_task — runs every Monday, sends WEEKLY reminders due this week.

    Channel: settings.alert_channel_id.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("reminder.daily")
        self._monitor.register_task("reminder.weekly")
        self._daily_task.start()
        self._weekly_task.start()
        logger.info("scheduler.reminder.started")

    def stop(self) -> None:
        self._daily_task.cancel()
        self._weekly_task.cancel()
        logger.info("scheduler.reminder.stopped")

    @tasks.loop(time=_REMINDER_DAILY_TIME)
    async def _daily_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:
            return
        await self._run_reminders(frequency="daily", task_name="reminder.daily")

    @_daily_task.before_loop
    async def _before_daily(self) -> None:
        await self._client.wait_until_ready()

    @tasks.loop(time=_REMINDER_WEEKLY_TIME)
    async def _weekly_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() != 0:  # Monday only
            return
        await self._run_reminders(frequency="weekly", task_name="reminder.weekly")

    @_weekly_task.before_loop
    async def _before_weekly(self) -> None:
        await self._client.wait_until_ready()

    async def _run_reminders(self, frequency: str, task_name: str) -> None:
        user_id = getattr(settings, "scheduler_user_id", None)
        channel_id = settings.alert_channel_id or None
        if not user_id or not channel_id:
            logger.warning(
                "scheduler.reminder.skipped",
                frequency=frequency,
                reason="scheduler_user_id or alert_channel_id not configured",
            )
            return

        try:
            from src.watchlist.reminder_service import ReminderService

            async with AsyncSessionLocal() as session:
                svc = ReminderService(session)
                reminders = await svc.get_due_reminders(
                    user_id=str(user_id), frequency=frequency
                )
                await session.commit()

            if not reminders:
                await self._monitor.record_success(task_name)
                return

            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning("scheduler.reminder.channel_not_found", channel_id=channel_id)
                await self._monitor.record_failure(
                    task_name, RuntimeError(f"channel {channel_id} not found")
                )
                return

            embed = build_reminder_embed(reminders, frequency)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.reminder.notified",
                frequency=frequency,
                count=len(reminders),
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.reminder.error", frequency=frequency, error=str(exc))
            await self._monitor.record_failure(task_name, exc)


# ---------------------------------------------------------------------------
# OutcomeFillerScheduler
# ---------------------------------------------------------------------------

_OUTCOME_FILLER_TIME = datetime.time(hour=8, minute=5, tzinfo=datetime.UTC)  # 15:05 ICT


class OutcomeFillerScheduler:
    """Fill DecisionLog outcome fields daily at 15:05 ICT (weekdays).

    Runs after market close and 10 min before DecisionReplayScheduler (15:15)
    so replay always reads fresh outcome_pnl_pct / outcome_verdict data.

    Bot is a thin timing adapter only. All business logic lives in
    thesis.OutcomeFillerService. No Discord message is sent (silent job).

    Flow:
        1. OutcomeFillerService.fill_pending_outcomes(user_id)
           — find DecisionLogs past review_horizon_days with null outcome_pnl_pct
           — fetch quote.price from QuoteService
           — compute pnl_pct, classify OutcomeVerdict, persist
        2. session.commit()
        3. Log filled_count; record success/failure in SchedulerMonitor.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("decision.outcome_fill")
        self._fill_task.start()
        logger.info("scheduler.outcome_filler.started", time_ict="15:05")

    def stop(self) -> None:
        self._fill_task.cancel()
        logger.info("scheduler.outcome_filler.stopped")

    @tasks.loop(time=_OUTCOME_FILLER_TIME)
    async def _fill_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:
            return

        task_name = "decision.outcome_fill"
        user_id = getattr(settings, "scheduler_user_id", None)
        if not user_id:
            logger.warning(
                "scheduler.outcome_filler.skipped",
                reason="scheduler_user_id not configured",
            )
            return

        try:
            from src.platform.bootstrap import get_quote_service
            from src.thesis.outcome_filler_service import OutcomeFillerService

            async with AsyncSessionLocal() as session:
                svc = OutcomeFillerService(
                    session=session,
                    quote_service=get_quote_service(),
                )
                filled_count = await svc.fill_pending_outcomes(user_id=str(user_id))
                await session.commit()

            logger.info(
                "scheduler.outcome_filler.done",
                user_id=user_id,
                filled_count=filled_count,
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.outcome_filler.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_fill_task.before_loop
    async def _before_fill(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# DecisionReplayScheduler
# ---------------------------------------------------------------------------

_REPLAY_TIME = datetime.time(hour=8, minute=15, tzinfo=datetime.UTC)  # 15:15 ICT


class DecisionReplayScheduler:
    """Run daily decision replay at 15:15 ICT (after market close).

    Replays recent open/close decisions, compares against outcomes,
    extracts behavioral patterns for InvestorProfile learning.

    Channel: settings.morning_channel_id.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("decision.replay")
        self._replay_task.start()
        logger.info("scheduler.decision_replay.started")

    def stop(self) -> None:
        self._replay_task.cancel()
        logger.info("scheduler.decision_replay.stopped")

    @tasks.loop(time=_REPLAY_TIME)
    async def _replay_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:
            return

        task_name = "decision.replay"
        user_id = getattr(settings, "scheduler_user_id", None)
        channel_id = getattr(settings, "morning_channel_id", None)
        if not user_id:
            logger.warning(
                "scheduler.decision_replay.skipped",
                reason="scheduler_user_id not configured",
            )
            return

        try:
            from src.platform.bootstrap import get_replay_agent

            async with AsyncSessionLocal() as session:
                replay_result = await get_replay_agent().run(
                    user_id=str(user_id),
                    session=session,
                )
                await session.commit()

            if not replay_result:
                await self._monitor.record_success(task_name)
                return

            if not channel_id:
                logger.warning(
                    "scheduler.decision_replay.notify_skipped",
                    reason="morning_channel_id not configured",
                )
                await self._monitor.record_success(task_name)
                return

            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning(
                    "scheduler.decision_replay.channel_not_found",
                    channel_id=channel_id,
                )
                await self._monitor.record_failure(
                    task_name, RuntimeError(f"channel {channel_id} not found")
                )
                return

            embed = build_replay_embed(replay_result, now_utc)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info("scheduler.decision_replay.notified")
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.decision_replay.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_replay_task.before_loop
    async def _before_replay(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# MemoryConsolidatorScheduler
# ---------------------------------------------------------------------------

_MEMORY_CONSOLIDATE_TIME = datetime.time(hour=2, minute=0, tzinfo=datetime.UTC)  # 09:00 ICT


class MemoryConsolidatorScheduler:
    """Run weekly memory distillation every Sunday at 09:00 ICT.

    Calls MemoryConsolidator.run(session) — ai segment — to synthesise episodic
    AIInteractionLog rows from the past 7 days into a MemorySnapshot (semantic
    patterns: behavioral_patterns, cognitive_biases, strengths, blind_spots,
    confidence_calibration).

    Flow:
        1. get_memory_consolidator() — retrieve singleton from bootstrap.
           Returns None if scheduler_user_id is not configured → graceful skip.
        2. Open AsyncSessionLocal → call consolidator.run(session).
           MemoryConsolidator handles its own commit; session is closed here.
        3. Log snapshot_id + episode_count from the returned MemorySnapshot.
           Returns None when episodes < 3 or AI/DB error — both are non-fatal.
        4. Record success/failure in SchedulerMonitor.

    Graceful skip:
        - If get_memory_consolidator() returns None (scheduler_user_id not set).
        - If consolidator.run() returns None (not enough episodes or AI error).
        - All exceptions are caught and recorded; never blocks other schedulers.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("memory.consolidate")
        self._consolidate_task.start()
        logger.info("scheduler.memory_consolidator.started", time_ict="Sunday 09:00")

    def stop(self) -> None:
        self._consolidate_task.cancel()
        logger.info("scheduler.memory_consolidator.stopped")

    @tasks.loop(time=_MEMORY_CONSOLIDATE_TIME)
    async def _consolidate_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() != 6:
            return

        task_name = "memory.consolidate"

        from src.platform.bootstrap import get_memory_consolidator
        consolidator = get_memory_consolidator()
        if consolidator is None:
            logger.warning(
                "scheduler.memory_consolidator.skipped",
                reason="MemoryConsolidator not initialised — scheduler_user_id not set",
            )
            return

        try:
            async with AsyncSessionLocal() as session:
                snapshot = await consolidator.run(session)  # type: ignore[union-attr]

            if snapshot is None:
                await self._monitor.record_success(task_name)
                return

            logger.info(
                "scheduler.memory_consolidator.done",
                snapshot_id=snapshot.id,
                episode_count=snapshot.episode_count,
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.memory_consolidator.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_consolidate_task.before_loop
    async def _before_consolidate(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# AgendaBuilderScheduler
# ---------------------------------------------------------------------------

_AGENDA_TIME = datetime.time(hour=0, minute=30, tzinfo=datetime.UTC)  # 07:30 ICT


class AgendaBuilderScheduler:
    """Build daily investor agenda at 07:30 ICT (weekdays).

    Runs before InvestorProfileScheduler (08:20) and BriefingScheduler (08:30)
    so the agenda result is persisted before the morning brief fires.

    Flow:
        1. get_agenda_service_factory() — returns callable(session) -> AgendaService,
           or None if scheduler_user_id is not configured → graceful skip.
        2. factory(session) — instantiate AgendaService with agent + MemoryService.
        3. svc.build_agenda(user_id) — load context, call AgendaBuilderAgent, persist result.
        4. session.commit() — persist DailyAgendaResult.
        5. Log decide/watch/defer counts; record success/failure in SchedulerMonitor.

    Graceful skip:
        - If get_agenda_service_factory() returns None (scheduler_user_id not set).
        - If build_agenda() returns None (AI error or no data) — non-fatal.
        - All exceptions are caught and recorded; never blocks downstream schedulers.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("agenda.build")
        self._agenda_task.start()
        logger.info("scheduler.agenda.started", time_ict="07:30")

    def stop(self) -> None:
        self._agenda_task.cancel()
        logger.info("scheduler.agenda.stopped")

    @tasks.loop(time=_AGENDA_TIME)
    async def _agenda_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:
            return

        task_name = "agenda.build"
        user_id = getattr(settings, "scheduler_user_id", None)

        from src.platform.bootstrap import get_agenda_service_factory
        factory = get_agenda_service_factory()

        if not user_id or factory is None:
            logger.warning(
                "scheduler.agenda.skipped",
                reason="agenda_service_factory not initialised — scheduler_user_id not set",
            )
            return

        try:
            async with AsyncSessionLocal() as session:
                svc = factory(session)
                result = await svc.build_agenda(str(user_id))
                await session.commit()

            if result is None:
                logger.info("scheduler.agenda.no_result", user_id=user_id)
                await self._monitor.record_success(task_name)
                return

            logger.info(
                "scheduler.agenda.done",
                user_id=user_id,
                decide=len(result.decide),
                watch=len(result.watch),
                defer=len(result.defer),
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.agenda.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_agenda_task.before_loop
    async def _before_agenda(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# ProactiveWatchScheduler
# ---------------------------------------------------------------------------

# ICT = UTC+7  →  subtract 7h for UTC
_PROACTIVE_MORNING_TIME = datetime.time(hour=2,  minute=15, tzinfo=datetime.UTC)  # 09:15 ICT
_PROACTIVE_MIDDAY_TIME  = datetime.time(hour=4,  minute=15, tzinfo=datetime.UTC)  # 11:15 ICT
_PROACTIVE_PRE_ATC_TIME = datetime.time(hour=7,  minute=15, tzinfo=datetime.UTC)  # 14:15 ICT


class ProactiveWatchScheduler:
    """Emit ProactiveWatchRequestedEvent 3× per trading day.

    Owner: bot segment — thin timing adapter only. No scan logic here.

    Phases:
        morning  — 09:15 ICT  after market open settles
        midday   — 11:15 ICT  mid-session check
        pre_atc  — 14:15 ICT  15 min before ATC

    Event chain:
        ProactiveWatchRequestedEvent  [bot → watchlist]
          → watchlist.ProactiveWatchListener
            → ScanService.scan_user()
            → AlertService.process_triggered()
            → ProactiveWatchAlertFiredEvent × N  [watchlist → bot]
              → ProactiveWatchSubscriber → Discord

    Graceful skip:
        - Weekends (weekday >= 5).
        - scheduler_user_id not configured → warning log, no event emitted.
        - All exceptions caught and recorded; never blocks other schedulers.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("proactive_watch.morning")
        self._monitor.register_task("proactive_watch.midday")
        self._monitor.register_task("proactive_watch.pre_atc")
        self._morning_task.start()
        self._midday_task.start()
        self._pre_atc_task.start()
        logger.info(
            "scheduler.proactive_watch.started",
            phases=["09:15", "11:15", "14:15"],
        )

    def stop(self) -> None:
        self._morning_task.cancel()
        self._midday_task.cancel()
        self._pre_atc_task.cancel()
        logger.info("scheduler.proactive_watch.stopped")

    # ── morning: 09:15 ICT ──────────────────────────────────────────────────────────────────────────────────────

    @tasks.loop(time=_PROACTIVE_MORNING_TIME)
    async def _morning_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit(phase="morning", task_name="proactive_watch.morning")

    @_morning_task.before_loop
    async def _before_morning(self) -> None:
        await self._client.wait_until_ready()

    # ── midday: 11:15 ICT ──────────────────────────────────────────────────────────────────────────────────────

    @tasks.loop(time=_PROACTIVE_MIDDAY_TIME)
    async def _midday_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit(phase="midday", task_name="proactive_watch.midday")

    @_midday_task.before_loop
    async def _before_midday(self) -> None:
        await self._client.wait_until_ready()

    # ── pre_atc: 14:15 ICT ────────────────────────────────────────────────────────────────────────────────────

    @tasks.loop(time=_PROACTIVE_PRE_ATC_TIME)
    async def _pre_atc_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit(phase="pre_atc", task_name="proactive_watch.pre_atc")

    @_pre_atc_task.before_loop
    async def _before_pre_atc(self) -> None:
        await self._client.wait_until_ready()

    # ── shared emit ─────────────────────────────────────────────────────────────────────────────────────

    async def _emit(self, phase: str, task_name: str) -> None:
        """Emit ProactiveWatchRequestedEvent — bot's only responsibility here."""
        from src.platform.event_bus import get_event_bus
        from src.platform.events import ProactiveWatchRequestedEvent

        user_id = getattr(settings, "scheduler_user_id", None)
        if not user_id:
            logger.warning(
                "scheduler.proactive_watch.skipped",
                phase=phase,
                reason="scheduler_user_id not configured",
            )
            return

        try:
            bus = get_event_bus()
            await bus.publish(
                ProactiveWatchRequestedEvent(
                    user_id=str(user_id),
                    phase=phase,
                    triggered_by="scheduler",
                )
            )
            logger.info("scheduler.proactive_watch.event_emitted", phase=phase)
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error(
                "scheduler.proactive_watch.emit_error",
                phase=phase,
                error=str(exc),
            )
            await self._monitor.record_failure(task_name, exc)


# ---------------------------------------------------------------------------
# PortfolioSnapshotScheduler
# ---------------------------------------------------------------------------

# ICT = UTC+7  →  subtract 7h for UTC
_PORTFOLIO_SNAPSHOT_TIME = datetime.time(hour=1, minute=15, tzinfo=datetime.UTC)  # 08:15 ICT


class PortfolioSnapshotScheduler:
    """Emit PortfolioSnapshotRequestedEvent at 08:15 ICT (weekdays).

    Owner: bot segment — thin timing adapter only.
    All P&L logic lives in portfolio.PortfolioSnapshotListener.

    Timing:
        08:15 ICT — 20 min before IntelligenceEngineScheduler (08:35)
        This window allows PortfolioSnapshotListener to build the P&L snapshot
        and cache it in IntelligenceEngineListener before the engine cycle fires.

    Event chain:
        PortfolioSnapshotRequestedEvent  [bot → portfolio]
          → portfolio.PortfolioSnapshotListener
            → PnlService.get_portfolio_pnl()
            → PortfolioSnapshotReadyEvent (enriched)  [portfolio → core]
              → core.IntelligenceEngineListener._handle_portfolio_snapshot()
                → cached in _portfolio_context[user_id]
                  → injected into engine.run_cycle() context_hint at 08:35 ICT

    Graceful skip:
        - Weekends (weekday >= 5).
        - scheduler_user_id not configured → warning log, no event emitted.
        - All exceptions caught and recorded; never blocks other schedulers.

    No Discord output from this scheduler.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("portfolio_snapshot.morning")
        self._morning_task.start()
        logger.info("scheduler.portfolio_snapshot.started", time_ict="08:15")

    def stop(self) -> None:
        self._morning_task.cancel()
        logger.info("scheduler.portfolio_snapshot.stopped")

    @tasks.loop(time=_PORTFOLIO_SNAPSHOT_TIME)
    async def _morning_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit(phase="morning", task_name="portfolio_snapshot.morning")

    @_morning_task.before_loop
    async def _before_morning(self) -> None:
        await self._client.wait_until_ready()

    async def _emit(self, phase: str, task_name: str) -> None:
        """Emit PortfolioSnapshotRequestedEvent — bot's only responsibility here.

        portfolio.PortfolioSnapshotListener (subscribed at bootstrap) handles the rest:
        PnlService.get_portfolio_pnl() → build enriched snapshot → PortfolioSnapshotReadyEvent.
        """
        from src.platform.event_bus import get_event_bus
        from src.platform.events import PortfolioSnapshotRequestedEvent

        user_id = getattr(settings, "scheduler_user_id", None)
        if not user_id:
            logger.warning(
                "scheduler.portfolio_snapshot.skipped",
                phase=phase,
                reason="scheduler_user_id not configured",
            )
            return

        try:
            bus = get_event_bus()
            await bus.publish(
                PortfolioSnapshotRequestedEvent(
                    user_id=str(user_id),
                    phase=phase,
                    triggered_by="scheduler",
                )
            )
            logger.info("scheduler.portfolio_snapshot.event_emitted", phase=phase, user_id=str(user_id))
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error(
                "scheduler.portfolio_snapshot.emit_error",
                phase=phase,
                error=str(exc),
            )
            await self._monitor.record_failure(task_name, exc)


# ---------------------------------------------------------------------------
# IntelligenceEngineScheduler
# ---------------------------------------------------------------------------

# ICT = UTC+7  →  subtract 7h for UTC
_IE_MORNING_TIME = datetime.time(hour=1, minute=35, tzinfo=datetime.UTC)  # 08:35 ICT
_IE_EOD_TIME     = datetime.time(hour=8, minute=12, tzinfo=datetime.UTC)  # 15:12 ICT


class IntelligenceEngineScheduler:
    """Emit IntelligenceEngineRequestedEvent twice per trading day.

    Owner: bot segment — thin timing adapter only.
    All engine logic lives in src/core/intelligence_listener.py → src/core/engine.py.

    Timing:
        morning — 08:35 ICT  after ThesisMaintenance (08:30), before SignalEngine (08:40)
        eod     — 15:12 ICT  after SignalEngine.eod (15:10), before DecisionReplay (15:15)

    Flow:
        1. bus.publish(IntelligenceEngineRequestedEvent(user_id, phase, triggered_by))
        2. IntelligenceEngineListener (subscribed at bootstrap) calls engine.run_cycle()
           — builds SystemSnapshot → heuristic verdict → publishes IntelligenceEngineCompletedEvent
        3. bot subscriber handles Discord delivery.
        4. Record success/failure in SchedulerMonitor.

    No Discord output from this scheduler — delivery via IntelligenceEngineListener.

    Graceful skip:
        - Weekends (weekday >= 5).
        - scheduler_user_id not configured → warning log, no event emitted.
        - All exceptions caught and recorded; never blocks other schedulers.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("intelligence_engine.morning")
        self._monitor.register_task("intelligence_engine.eod")
        self._morning_task.start()
        self._eod_task.start()
        logger.info(
            "scheduler.intelligence_engine.started",
            phases=["08:35", "15:12"],
        )

    def stop(self) -> None:
        self._morning_task.cancel()
        self._eod_task.cancel()
        logger.info("scheduler.intelligence_engine.stopped")

    # ── morning: 08:35 ICT ──────────────────────────────────────────────────────────────────────────────────

    @tasks.loop(time=_IE_MORNING_TIME)
    async def _morning_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._run_cycle(phase="morning", task_name="intelligence_engine.morning")

    @_morning_task.before_loop
    async def _before_morning(self) -> None:
        await self._client.wait_until_ready()

    # ── eod: 15:12 ICT ───────────────────────────────────────────────────────────────────────────────────

    @tasks.loop(time=_IE_EOD_TIME)
    async def _eod_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._run_cycle(phase="eod", task_name="intelligence_engine.eod")

    @_eod_task.before_loop
    async def _before_eod(self) -> None:
        await self._client.wait_until_ready()

    # ── shared emit ─────────────────────────────────────────────────────────────────────────────────────

    async def _run_cycle(self, phase: str, task_name: str) -> None:
        """Emit IntelligenceEngineRequestedEvent — bot's only responsibility here.

        IntelligenceEngineListener (subscribed at bootstrap) handles the rest:
        engine.run_cycle() → SystemSnapshot → verdict → IntelligenceEngineCompletedEvent → Discord.
        """
        from src.platform.event_bus import get_event_bus
        from src.platform.events import IntelligenceEngineRequestedEvent

        user_id = getattr(settings, "scheduler_user_id", None)
        if not user_id:
            logger.warning(
                "scheduler.intelligence_engine.skipped",
                phase=phase,
                reason="scheduler_user_id not configured",
            )
            return

        try:
            bus = get_event_bus()
            await bus.publish(
                IntelligenceEngineRequestedEvent(
                    user_id=str(user_id),
                    trigger_type="scheduled",
                    trigger_source=f"scheduler.{phase}",
                )
            )
            logger.info("scheduler.intelligence_engine.event_emitted", phase=phase)
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error(
                "scheduler.intelligence_engine.emit_error",
                phase=phase,
                error=str(exc),
            )
            await self._monitor.record_failure(task_name, exc)
