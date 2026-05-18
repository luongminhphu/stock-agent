"""Scheduler — orchestrate recurring bot tasks.

Owner: bot segment (adapter only).
No business logic — calls domain services on schedule.

Registered tasks:
    InvestorProfileScheduler.snapshot_task     — weekdays 08:20 ICT (before maintenance + brief)
    BriefingScheduler.morning_brief_task       — weekdays 08:45 ICT
    BriefingScheduler.eod_brief_task           — weekdays 15:05 ICT
    WatchlistScanScheduler.scan_task           — every 5 min, weekdays 09:00–15:00 ICT
    ThesisMaintenanceScheduler.maintenance     — weekdays 08:30 ICT (before morning brief)
    ThesisDriftScheduler.drift_task            — every 15 min, weekdays 09:00–15:00 ICT
    ReminderScheduler.daily_task               — weekdays 08:00 ICT (DAILY reminders)
    ReminderScheduler.weekly_task              — Mondays 08:00 ICT (WEEKLY reminders)
    DecisionReplayScheduler.replay_task        — weekdays 15:15 ICT (after market close)
    MemoryConsolidatorScheduler.consolidate    — Sundays 09:00 ICT (weekly memory distill)
    SignalEngineScheduler.morning_task         — weekdays 08:40 ICT (before morning brief)
    SignalEngineScheduler.eod_task             — weekdays 15:10 ICT (after market close)

Note:
    MORNING_CHANNEL_ID and EOD_CHANNEL_ID must be set in settings.
    SCHEDULER_USER_ID is the service account used for scheduled tasks.

Channel routing:
    Briefing, ThesisMaintenance, InvestorProfile, DecisionReplay → morning_channel_id
    Proactive alerts (WatchlistScan, ThesisDrift, Reminder)      → alert_channel_id
        alert_channel_id = DISCORD_ALERT_CHANNEL_ID if set, else morning_channel_id
    SignalEngineScheduler emits events only — no channel routing, no Discord message.

Wave 8:
    BriefingScheduler no longer calls BriefingService directly.
    It emits BriefingRequestedEvent → BriefingListener (briefing segment)
    handles delivery. Bot is a thin timing adapter only.

Wave 2 (Signal Engine):
    SignalEngineScheduler emits SignalEngineRequestedEvent → ai.SignalEngineListener
    runs watchlist × thesis × portfolio cross-check → emits SignalEngineCompletedEvent
    → briefing.BriefingListener injects summary into brief context.
"""

from __future__ import annotations

import datetime

import discord
from discord.ext import tasks

from src.bot.commands.decision_embeds import build_replay_embed
from src.bot.commands.reminder_embeds import build_reminder_embed
from src.bot.commands.thesis_embeds import build_drift_embed, build_maintenance_embed
from src.bot.commands.watchlist_embeds import build_scan_embed
from src.platform.bootstrap import (
    get_investor_profile_service,
    get_memory_consolidator,
    get_quote_service,
    get_replay_agent,
    get_thesis_review_agent,
)
from src.platform.config import settings
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.platform.scheduler_monitor import SchedulerMonitor, get_monitor

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
    25 min before BriefingScheduler (08:45) so the morning brief
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

_MORNING_TIME = datetime.time(hour=1, minute=45, tzinfo=datetime.UTC)  # 08:45 ICT
_EOD_TIME     = datetime.time(hour=8, minute=5,  tzinfo=datetime.UTC)  # 15:05 ICT


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

        # ── Step 0: Reactivate cooled-down alerts (isolated commit) ────────────────────────────────────────────────
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

        # ── Step 1: Scan ─────────────────────────────────────────────────────────────────────────────────────
        try:
            from src.thesis.ticker_direction_query import TickerDirectionQuery
            from src.watchlist.scan_service import ScanService

            async with AsyncSessionLocal() as session:
                svc = ScanService(
                    session=session,
                    quote_service=get_quote_service(),
                    ticker_direction_query=TickerDirectionQuery(session),
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

_MAINTENANCE_TIME      = datetime.time(hour=1, minute=30, tzinfo=datetime.UTC)  # 08:30 ICT
_MAINTENANCE_STALE_DAYS = 3
_CATALYST_LOOKAHEAD_DAYS = 7   # fetch catalysts within this window


class ThesisMaintenanceScheduler:
    """Chạy lúc 08:30 ICT mỗi ngày làm việc — 15 phút trước morning brief.

    Flow:
        1.  auto_expire_overdue_catalysts()  — không tốn token, chạy đầu tiên.
        1b. get_upcoming_catalysts()         — lấy catalyst sắp đến trong 7 ngày.
                                               Isolated session, non-blocking.
        2.  review_stale_theses()            — AI review, chỉ khi thesis stale > 3 ngày.
        3.  Discord notify nếu có thay đổi hoặc có upcoming catalysts chưa notified hôm nay.

    Hai bước dùng session riêng biệt — expire và review độc lập, bước 2
    fail không rollback bước 1.

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
            return

        # -- Step 1b: Fetch upcoming catalysts (no AI, isolated session, non-blocking) --
        try:
            from src.readmodel.thesis_query_service import ThesisQueryService

            async with AsyncSessionLocal() as session:
                query_svc = ThesisQueryService(session)
                all_upcoming = await query_svc.get_upcoming_catalysts(
                    str(user_id), days=_CATALYST_LOOKAHEAD_DAYS
                )

            # Deduplicate: only keep catalysts not yet notified today
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
            upcoming_catalysts = []  # non-blocking — continue with empty list

        # -- Step 2: AI review for stale theses --
        try:
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

        # -- Step 3: Discord notify — presentation delegated to thesis_embeds --
        has_content = expired_count > 0 or reviews or upcoming_catalysts
        if not channel_id or not has_content:
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

            # Mark successfully notified catalyst IDs so they are not re-sent today
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
            from src.thesis.conviction_drift_detector import ConvictionDriftDetector
            from src.thesis.drift_service import DriftService
            from src.thesis.review_service import ReviewService

            # -- Step 1a: Detect price-drifted theses (no AI) --
            async with AsyncSessionLocal() as session:
                drift_svc = DriftService(
                    session=session,
                    quote_service=get_quote_service(),
                    threshold_pct=settings.thesis_drift_threshold_pct,
                    cooldown_hours=settings.thesis_drift_cooldown_hours,
                )
                signals = await drift_svc.detect(str(user_id))

            # -- Step 1b: Detect conviction drift (no AI, non-blocking) --
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

            # -- Step 2: AI review for price-drifted theses --
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

            # -- Step 3: Discord notify --
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
            agent = get_replay_agent()
            if agent is None:
                logger.warning(
                    "scheduler.decision_replay.skipped",
                    reason="replay_agent not initialised",
                )
                return

            async with AsyncSessionLocal() as session:
                result = await agent.run(user_id=str(user_id), session=session)
                await session.commit()

            if not result or not result.replays:
                await self._monitor.record_success(task_name)
                return

            if not channel_id:
                await self._monitor.record_success(task_name)
                return

            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning(
                    "scheduler.decision_replay.channel_not_found", channel_id=channel_id
                )
                await self._monitor.record_failure(
                    task_name, RuntimeError(f"channel {channel_id} not found")
                )
                return

            embed = build_replay_embed(result, now_utc)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.decision_replay.notified", replay_count=len(result.replays)
            )
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

_CONSOLIDATE_TIME = datetime.time(hour=2, minute=0, tzinfo=datetime.UTC)  # 09:00 ICT Sunday


class MemoryConsolidatorScheduler:
    """Distill weekly memory snapshots every Sunday at 09:00 ICT.

    Reads raw interaction + decision logs from the past week,
    extracts persistent behavioral patterns, and writes a
    consolidated MemorySnapshot for InvestorProfile.

    No Discord output — pure data pipeline, no channel routing.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("memory.consolidate")
        self._consolidate_task.start()
        logger.info("scheduler.memory_consolidator.started")

    def stop(self) -> None:
        self._consolidate_task.cancel()
        logger.info("scheduler.memory_consolidator.stopped")

    @tasks.loop(time=_CONSOLIDATE_TIME)
    async def _consolidate_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() != 6:  # Sunday only
            return

        task_name = "memory.consolidate"
        user_id = getattr(settings, "scheduler_user_id", None)
        if not user_id:
            logger.warning(
                "scheduler.memory_consolidator.skipped",
                reason="scheduler_user_id not configured",
            )
            return

        try:
            consolidator = get_memory_consolidator()
            if consolidator is None:
                logger.warning(
                    "scheduler.memory_consolidator.skipped",
                    reason="memory_consolidator not initialised",
                )
                return

            async with AsyncSessionLocal() as session:
                result = await consolidator.run(user_id=str(user_id), session=session)
                await session.commit()

            logger.info(
                "scheduler.memory_consolidator.done",
                patterns_extracted=getattr(result, "patterns_extracted", None),
            )
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.memory_consolidator.error", error=str(exc))
            await self._monitor.record_failure(task_name, exc)

    @_consolidate_task.before_loop
    async def _before_consolidate(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# SignalEngineScheduler
# ---------------------------------------------------------------------------

_SIGNAL_MORNING_TIME = datetime.time(hour=1, minute=40, tzinfo=datetime.UTC)  # 08:40 ICT
_SIGNAL_EOD_TIME     = datetime.time(hour=8, minute=10, tzinfo=datetime.UTC)  # 15:10 ICT


class SignalEngineScheduler:
    """Emit SignalEngineRequestedEvent twice daily — before morning brief and after close.

    Wave 2: bot is a thin timing adapter only.
    SignalEngineListener (ai segment) handles the cross-check logic.
    No channel routing — emits events only.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("signal_engine.morning")
        self._monitor.register_task("signal_engine.eod")
        self._morning_task.start()
        self._eod_task.start()
        logger.info("scheduler.signal_engine.started")

    def stop(self) -> None:
        self._morning_task.cancel()
        self._eod_task.cancel()
        logger.info("scheduler.signal_engine.stopped")

    @tasks.loop(time=_SIGNAL_MORNING_TIME)
    async def _morning_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit_signal_engine(phase="morning")

    @_morning_task.before_loop
    async def _before_morning(self) -> None:
        await self._client.wait_until_ready()

    @tasks.loop(time=_SIGNAL_EOD_TIME)
    async def _eod_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit_signal_engine(phase="eod")

    @_eod_task.before_loop
    async def _before_eod(self) -> None:
        await self._client.wait_until_ready()

    async def _emit_signal_engine(self, phase: str) -> None:
        from src.platform.event_bus import get_event_bus
        from src.platform.events import SignalEngineRequestedEvent

        task_name = f"signal_engine.{phase}"
        user_id = getattr(settings, "scheduler_user_id", None)

        if not user_id:
            logger.warning(
                "scheduler.signal_engine.skipped",
                phase=phase,
                reason="scheduler_user_id not configured",
            )
            return

        try:
            bus = get_event_bus()
            await bus.publish(
                SignalEngineRequestedEvent(
                    phase=phase,
                    triggered_by="scheduler",
                )
            )
            logger.info("scheduler.signal_engine.event_emitted", phase=phase)
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.signal_engine.emit_error", phase=phase, error=str(exc))
            await self._monitor.record_failure(task_name, exc)
