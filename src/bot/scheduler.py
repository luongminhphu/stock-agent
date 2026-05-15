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

        # ── Step 0: Reactivate cooled-down alerts (isolated commit) ───────────────────────
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

        # ── Step 1: Scan ─────────────────────────────────────────────────────────────────
        try:
            from src.watchlist.scan_service import ScanService

            async with AsyncSessionLocal() as session:
                svc = ScanService(
                    session=session,
                    quote_service=get_quote_service(),
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


class ThesisMaintenanceScheduler:
    """Chạy lúc 08:30 ICT mỗi ngày làm việc — 15 phút trước morning brief.

    Flow:
        1. auto_expire_overdue_catalysts()  — không tốn token, chạy đầu tiên.
        2. review_stale_theses()            — AI review, chỉ khi thesis stale > 3 ngày.
        3. Discord notify nếu có thay đổi.

    Hai bước dùng session riêng biệt — expire và review độc lập, bước 2
    fail không rollback bước 1.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

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

        expired_count = 0
        reviews: list = []

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
        if not channel_id or (expired_count == 0 and not reviews):
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
            )
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info("scheduler.thesis_maintenance.notified")
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
                # non-blocking — price drift flow continues normally

            # -- Guard: nothing to do --
            has_price_signals      = bool(signals)
            has_conviction_signals = bool(conviction_signals)

            if not has_price_signals and not has_conviction_signals:
                logger.debug("scheduler.drift.no_signals", user_id=user_id)
                await self._monitor.record_success(task_name)
                return

            if has_price_signals:
                logger.info(
                    "scheduler.drift.price_signals_found",
                    count=len(signals),
                    tickers=[s.ticker for s in signals],
                )

            # -- Step 2: AI review per price-drifted thesis (sequential, rate-limit safe) --
            # Conviction-only signals do NOT trigger AI review in this wave.
            reviews: list[tuple] = []
            if has_price_signals:
                for signal in signals:
                    try:
                        async with AsyncSessionLocal() as session:
                            review_svc = ReviewService(
                                session=session,
                                agent=get_thesis_review_agent(),  # type: ignore[arg-type]
                                quote_service=get_quote_service(),
                            )
                            review = await review_svc.review_thesis(
                                thesis_id=signal.thesis_id,
                                user_id=signal.user_id,
                                current_price=signal.current_price,
                            )
                            await session.commit()
                        reviews.append((signal, review))
                        logger.info(
                            "scheduler.drift.review_done",
                            thesis_id=signal.thesis_id,
                            ticker=signal.ticker,
                            verdict=review.verdict,
                            drift_pct=signal.drift_pct,
                        )
                    except Exception as exc:
                        logger.warning(
                            "scheduler.drift.review_failed",
                            thesis_id=signal.thesis_id,
                            ticker=signal.ticker,
                            error=str(exc),
                        )

            # -- Guard: skip notify if no content to show --
            if not reviews and not has_conviction_signals:
                # All price reviews failed AND no conviction signals — nothing to send.
                await self._monitor.record_success(task_name)
                return

            # -- Step 3: Discord notify — presentation delegated to thesis_embeds --
            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning("scheduler.drift.channel_not_found", channel_id=channel_id)
                await self._monitor.record_failure(
                    task_name, RuntimeError(f"channel {channel_id} not found")
                )
                return

            embed = build_drift_embed(reviews, now_utc, conviction_signals=conviction_signals)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.drift.notified",
                reviewed=len(reviews),
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

_REMINDER_DAILY_TIME  = datetime.time(hour=1, minute=0, tzinfo=datetime.UTC)   # 08:00 ICT
_REMINDER_WEEKLY_TIME = datetime.time(hour=1, minute=0, tzinfo=datetime.UTC)   # 08:00 ICT Monday


class ReminderScheduler:
    """Fire watchlist reminders via Discord based on investor-set frequency.

    Channel: settings.alert_channel_id (DISCORD_ALERT_CHANNEL_ID → morning_channel_id).
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
        await self._fire_reminders(label="daily")

    @_daily_task.before_loop
    async def _before_daily(self) -> None:
        await self._client.wait_until_ready()

    @tasks.loop(time=_REMINDER_WEEKLY_TIME)
    async def _weekly_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() != 0:
            return
        await self._fire_reminders(label="weekly")

    @_weekly_task.before_loop
    async def _before_weekly(self) -> None:
        await self._client.wait_until_ready()

    async def _fire_reminders(self, label: str) -> None:
        from src.watchlist.models import ReminderFrequency
        from src.watchlist.reminder_service import ReminderService

        task_name = f"reminder.{label}"
        channel_id = settings.alert_channel_id or None
        if not channel_id:
            logger.warning("scheduler.reminder.skipped", label=label, reason="alert_channel_id not configured")
            return

        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            logger.warning("scheduler.reminder.channel_not_found", channel_id=channel_id)
            await self._monitor.record_failure(
                task_name, RuntimeError(f"channel {channel_id} not found")
            )
            return

        frequency_map = {
            "daily":  [ReminderFrequency.DAILY],
            "weekly": [ReminderFrequency.WEEKLY],
        }
        frequencies = frequency_map.get(label, [ReminderFrequency.DAILY])
        freq_label = "hàng ngày" if label == "daily" else "hàng tuần"

        # -- Step 1: Fetch due reminders (read-only session, closed after) --
        try:
            async with AsyncSessionLocal() as session:
                svc = ReminderService(session)
                due = await svc.list_due(frequencies=frequencies)

            if not due:
                logger.debug("scheduler.reminder.none_due", label=label)
                await self._monitor.record_success(task_name)
                return

            logger.info("scheduler.reminder.firing", label=label, count=len(due))
        except Exception as exc:
            logger.error("scheduler.reminder.list_failed", label=label, error=str(exc))
            await self._monitor.record_failure(task_name, exc)
            return

        # -- Step 2: Send + mark_sent per reminder with isolated commit --
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
        sent_count = 0

        for reminder in due:
            ticker = (
                reminder.watchlist_item.ticker
                if reminder.watchlist_item
                else f"item#{reminder.watchlist_item_id}"
            )
            try:
                embed = build_reminder_embed(ticker, freq_label, ict_time)
                await channel.send(embed=embed)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error(
                    "scheduler.reminder.send_failed",
                    ticker=ticker,
                    reminder_id=reminder.id,
                    error=str(exc),
                )
                continue

            try:
                async with AsyncSessionLocal() as mark_session:
                    mark_svc = ReminderService(mark_session)
                    await mark_svc.mark_sent_by_id(reminder.id)
                    await mark_session.commit()
                logger.info(
                    "scheduler.reminder.sent",
                    ticker=ticker,
                    reminder_id=reminder.id,
                    frequency=reminder.frequency,
                )
                sent_count += 1
            except Exception as exc:
                logger.error(
                    "scheduler.reminder.mark_sent_failed",
                    ticker=ticker,
                    reminder_id=reminder.id,
                    error=str(exc),
                )

        logger.info("scheduler.reminder.done", label=label, sent=sent_count, total=len(due))
        await self._monitor.record_success(task_name)


# ---------------------------------------------------------------------------
# DecisionReplayScheduler
# ---------------------------------------------------------------------------

_REPLAY_TIME = datetime.time(hour=8, minute=15, tzinfo=datetime.UTC)  # 15:15 ICT


class DecisionReplayScheduler:
    """Auto-evaluate decision outcomes and run AI replay after market close.

    Owner: bot segment (adapter only).
    All domain logic lives in thesis.DecisionService and ai.ReplayAgent.

    Flow (runs weekdays at 15:15 ICT):
        1. DecisionService.list_pending_outcome_evaluations()
        2. For each pending decision:
           a. evaluate_outcome(id)   — compute realized PnL + assign verdict (no AI).
           b. analyze_decision(id)   — call ReplayAgent for key_lesson + pattern.
           c. persist_lesson(id, replay_result) — write key_lesson + pattern_detected
              back to DecisionLog so LessonService can surface them in future prompts.
        3. Notify Discord with a summary embed.
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("decision.replay")
        self._replay_task.start()
        logger.info("scheduler.decision_replay.started", time_ict="15:15")

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
        if not user_id or not channel_id:
            logger.warning(
                "scheduler.decision_replay.skipped",
                reason="scheduler_user_id or morning_channel_id not configured",
            )
            return

        from src.thesis.decision_service import DecisionService

        # -- Step 1: Find decisions that reached their horizon --
        try:
            async with AsyncSessionLocal() as session:
                svc = DecisionService(
                    session=session,
                    quote_service=get_quote_service(),
                )
                pending = await svc.list_pending_outcome_evaluations()
        except Exception as exc:
            logger.error("scheduler.decision_replay.list_failed", error=str(exc))
            await self._monitor.record_failure(task_name, exc)
            return

        if not pending:
            logger.debug("scheduler.decision_replay.none_pending")
            await self._monitor.record_success(task_name)
            return

        logger.info(
            "scheduler.decision_replay.pending_found",
            count=len(pending),
            decision_ids=[d.id for d in pending],
        )

        # -- Step 2: Evaluate + Replay + Persist lesson per decision --
        results: list[dict] = []
        for decision in pending:
            # 2a: evaluate realized outcome (no AI)
            try:
                async with AsyncSessionLocal() as session:
                    svc = DecisionService(
                        session=session,
                        quote_service=get_quote_service(),
                    )
                    evaluated = await svc.evaluate_outcome(decision.id)
                    await session.commit()
                logger.info(
                    "scheduler.decision_replay.evaluated",
                    decision_id=evaluated.id,
                    ticker=evaluated.ticker,
                    pnl_pct=evaluated.outcome_pnl_pct,
                    verdict=evaluated.outcome_verdict,
                )
            except Exception as exc:
                logger.warning(
                    "scheduler.decision_replay.evaluate_failed",
                    decision_id=decision.id,
                    ticker=decision.ticker,
                    error=str(exc),
                )
                continue

            # 2b: AI replay analysis
            replay_result = None
            try:
                async with AsyncSessionLocal() as session:
                    svc = DecisionService(
                        session=session,
                        quote_service=get_quote_service(),
                        replay_agent=get_replay_agent(),
                    )
                    envelope = await svc.analyze_decision(decision.id)
                    replay_result = envelope.replay
            except Exception as exc:
                logger.warning(
                    "scheduler.decision_replay.analyze_failed",
                    decision_id=decision.id,
                    ticker=decision.ticker,
                    error=str(exc),
                )

            # 2c: Persist lesson back to DecisionLog for LessonService
            if replay_result is not None:
                try:
                    async with AsyncSessionLocal() as session:
                        svc = DecisionService(
                            session=session,
                            quote_service=get_quote_service(),
                        )
                        await svc.persist_lesson(
                            decision_id=decision.id,
                            key_lesson=getattr(replay_result, "key_lesson", None),
                            pattern_detected=getattr(replay_result, "pattern_detected", None),
                        )
                        await session.commit()
                    logger.info(
                        "scheduler.decision_replay.lesson_persisted",
                        decision_id=decision.id,
                        ticker=decision.ticker,
                    )
                except Exception as exc:
                    logger.warning(
                        "scheduler.decision_replay.lesson_persist_failed",
                        decision_id=decision.id,
                        ticker=decision.ticker,
                        error=str(exc),
                    )

            results.append({
                "decision": evaluated,
                "replay": replay_result,
            })

        if not results:
            return

        await self._monitor.record_success(task_name)

        # -- Step 3: Discord notify — presentation delegated to decision_embeds --
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            logger.warning("scheduler.decision_replay.channel_not_found", channel_id=channel_id)
            return

        try:
            embed = build_replay_embed(results, now_utc)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.decision_replay.notified",
                count=len(results),
            )
        except Exception as exc:
            logger.error("scheduler.decision_replay.notify_failed", error=str(exc))

    @_replay_task.before_loop
    async def _before_replay(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# MemoryConsolidatorScheduler
# ---------------------------------------------------------------------------

_MEMORY_CONSOLIDATE_TIME = datetime.time(hour=2, minute=0, tzinfo=datetime.UTC)  # 09:00 ICT Sunday


class MemoryConsolidatorScheduler:
    """Weekly memory distillation: episodic logs → MemorySnapshot.

    Owner: bot segment (adapter only).
    All domain logic lives in ai.memory.MemoryConsolidator.

    Schedule: Every Sunday at 09:00 ICT (02:00 UTC).
    """

    def __init__(self, client: discord.Client, monitor: SchedulerMonitor | None = None) -> None:
        self._client = client
        self._monitor = monitor or get_monitor()

    def start(self) -> None:
        self._monitor.register_task("memory.consolidate")
        self._consolidate_task.start()
        logger.info("scheduler.memory_consolidator.started", time_utc="Sunday 02:00")

    def stop(self) -> None:
        self._consolidate_task.cancel()
        logger.info("scheduler.memory_consolidator.stopped")

    @tasks.loop(time=_MEMORY_CONSOLIDATE_TIME)
    async def _consolidate_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() != 6:
            return

        task_name = "memory.consolidate"

        consolidator = get_memory_consolidator()
        if consolidator is None:
            logger.warning(
                "scheduler.memory_consolidator.skipped",
                reason="consolidator not initialised — scheduler_user_id not set",
            )
            return

        logger.info("scheduler.memory_consolidator.running")

        try:
            async with AsyncSessionLocal() as session:
                snapshot = await consolidator.run(session)

            if snapshot is not None:
                logger.info(
                    "scheduler.memory_consolidator.done",
                    snapshot_id=snapshot.id,
                    episode_count=snapshot.episode_count,
                    period_start=str(snapshot.period_start.date()),
                    period_end=str(snapshot.period_end.date()),
                )
                await self._monitor.record_success(task_name)
            else:
                logger.info(
                    "scheduler.memory_consolidator.skipped_by_consolidator",
                    reason="not enough episodes or AI call failed",
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

_SIGNAL_ENGINE_MORNING_TIME = datetime.time(hour=1, minute=40, tzinfo=datetime.UTC)  # 08:40 ICT
_SIGNAL_ENGINE_EOD_TIME     = datetime.time(hour=8, minute=10, tzinfo=datetime.UTC)  # 15:10 ICT


class SignalEngineScheduler:
    """Trigger AI signal engine before morning brief and after market close.

    Owner: bot segment (adapter only).
    No business logic — emits SignalEngineRequestedEvent only.
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

    @tasks.loop(time=_SIGNAL_ENGINE_MORNING_TIME)
    async def _morning_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit(phase="morning")

    @_morning_task.before_loop
    async def _before_morning(self) -> None:
        await self._client.wait_until_ready()

    @tasks.loop(time=_SIGNAL_ENGINE_EOD_TIME)
    async def _eod_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._emit(phase="eod")

    @_eod_task.before_loop
    async def _before_eod(self) -> None:
        await self._client.wait_until_ready()

    async def _emit(self, phase: str) -> None:
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
                    user_id=str(user_id),
                )
            )
            logger.info("scheduler.signal_engine.event_emitted", phase=phase)
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.signal_engine.emit_error", phase=phase, error=str(exc))
            await self._monitor.record_failure(task_name, exc)


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

Scheduler = BriefingScheduler
