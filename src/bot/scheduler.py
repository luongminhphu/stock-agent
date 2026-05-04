"""Scheduler — orchestrate recurring bot tasks.

Owner: bot segment (adapter only).
No business logic — calls domain services on schedule.

Registered tasks:
    BriefingScheduler.morning_brief_task       — weekdays 08:45 ICT
    BriefingScheduler.eod_brief_task           — weekdays 15:05 ICT
    WatchlistScanScheduler.scan_task           — every 5 min, weekdays 09:00–15:00 ICT
    ThesisMaintenanceScheduler.maintenance     — weekdays 08:30 ICT (before morning brief)
    ThesisDriftScheduler.drift_task            — every 15 min, weekdays 09:00–15:00 ICT
    ReminderScheduler.daily_task               — weekdays 08:00 ICT (DAILY reminders)
    ReminderScheduler.weekly_task              — Mondays 08:00 ICT (WEEKLY reminders)
    DecisionReplayScheduler.replay_task        — weekdays 15:15 ICT (after market close)

Note:
    MORNING_CHANNEL_ID and EOD_CHANNEL_ID must be set in settings.
    SCHEDULER_USER_ID is the service account used for scheduled tasks.
"""

from __future__ import annotations

import datetime

import discord
from discord.ext import tasks

from src.bot.commands.briefing import build_brief_embed
from src.bot.commands.thesis_embeds import build_maintenance_embed
from src.briefing.service import BriefingService
from src.platform.bootstrap import (
    get_briefing_agent,
    get_pnl_service,
    get_quote_service,
    get_replay_agent,
    get_thesis_review_agent,
)
from src.platform.config import settings
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.platform.scheduler_monitor import SchedulerMonitor, get_monitor
from src.watchlist.service import WatchlistService

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
# BriefingScheduler
# ---------------------------------------------------------------------------

_MORNING_TIME = datetime.time(hour=1, minute=45, tzinfo=datetime.UTC)  # 08:45 ICT
_EOD_TIME     = datetime.time(hour=8, minute=5,  tzinfo=datetime.UTC)  # 15:05 ICT


class BriefingScheduler:
    """Attach to a discord.Client after login."""

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
        await self._send_brief(phase="morning")

    @_morning_task.before_loop
    async def _before_morning(self) -> None:
        await self._client.wait_until_ready()

    @tasks.loop(time=_EOD_TIME)
    async def _eod_task(self) -> None:
        if datetime.datetime.now(tz=datetime.UTC).weekday() >= 5:
            return
        await self._send_brief(phase="eod")

    @_eod_task.before_loop
    async def _before_eod(self) -> None:
        await self._client.wait_until_ready()

    async def _send_brief(self, phase: str) -> None:
        task_name = f"briefing.{phase}"
        channel_id = getattr(
            settings, "morning_channel_id" if phase == "morning" else "eod_channel_id", None
        )
        user_id = getattr(settings, "scheduler_user_id", None)

        if not channel_id or not user_id:
            logger.warning(
                "scheduler.briefing.skipped",
                phase=phase,
                reason="channel_id or user_id not configured",
            )
            return

        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            logger.warning("scheduler.briefing.channel_not_found", channel_id=channel_id)
            return

        try:
            async with AsyncSessionLocal() as session:
                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=get_quote_service(),
                    briefing_agent=get_briefing_agent(),
                    pnl_service=get_pnl_service(),
                    session=session,
                )
                if phase == "morning":
                    brief = await svc.generate_morning_brief(user_id=str(user_id))
                else:
                    brief = await svc.generate_eod_brief(user_id=str(user_id))
                await session.commit()

            embed = build_brief_embed(brief, phase=phase)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info("scheduler.briefing.sent", phase=phase, channel_id=channel_id)
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.briefing.error", phase=phase, error=str(exc))
            await self._monitor.record_failure(task_name, exc)


# ---------------------------------------------------------------------------
# WatchlistScanScheduler
# ---------------------------------------------------------------------------

_SCAN_INTERVAL_MINUTES = 5


class WatchlistScanScheduler:
    """Scan watchlist every 5 minutes during market hours and notify Discord.

    - Runs weekdays 09:00–15:00 ICT only (silent outside market hours).
    - Sends embed only when signals exist (alert_triggered or strong_move).
    - ON_SIGNAL reminders piggyback on the same embed — no extra message.
    - Does NOT call AI — zero token cost.
    - Reuses morning_channel_id + scheduler_user_id from settings.
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
        channel_id = getattr(settings, "morning_channel_id", None)
        if not user_id or not channel_id:
            logger.warning(
                "scheduler.scan.skipped",
                reason="scheduler_user_id or morning_channel_id not configured",
            )
            return

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
                return

            lines: list[str] = []
            for s in result.signals:
                icon = "🔔" if s.has_alerts else "📊"
                lines.append(f"{icon} **{s.ticker}** {s.change_pct:+.1f}% — {s.description}")

            for r in result.on_signal_reminders:
                ticker = r.watchlist_item.ticker if r.watchlist_item else f"item#{r.watchlist_item_id}"
                lines.append(f"⏰ **{ticker}** — nhắc nhở theo dõi (ON_SIGNAL)")

            has_triggered = result.triggered_count > 0
            embed = discord.Embed(
                title="📡 Watchlist Scan",
                description="\n".join(lines),
                color=0xFF6B35 if has_triggered else 0x4F98A3,
            )
            ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
            signal_count = len(result.signals)
            reminder_count = len(result.on_signal_reminders)
            footer_parts = [f"Scan lúc {ict_time}"]
            if signal_count:
                footer_parts.append(f"{signal_count} tín hiệu")
            if reminder_count:
                footer_parts.append(f"{reminder_count} nhắc nhở")
            embed.set_footer(text=" — ".join(footer_parts))

            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.scan.notified",
                signals=signal_count,
                triggered=result.triggered_count,
                on_signal_reminders=reminder_count,
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

        # ── Step 1: Auto-expire overdue catalysts (no AI, no token cost) ──
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

        # ── Step 2: AI review for stale theses ──
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

        # ── Step 3: Discord notify — presentation delegated to thesis_embeds ──
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
    """Detect thesis price drift every 15 min during market hours and trigger AI review.

    Flow (per tick):
        1. DriftService.detect() — pure detection, no AI, no state mutation.
        2. For each DriftSignal: ReviewService.review_thesis() — AI review + snapshot.
        3. Discord notify with verdict + drift summary per thesis.

    Cooldown is enforced inside DriftService (default 4h) — ReviewService is
    never called twice for the same thesis within the cooldown window.
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
        channel_id = getattr(settings, "morning_channel_id", None)
        if not user_id or not channel_id:
            logger.warning(
                "scheduler.drift.skipped",
                reason="scheduler_user_id or morning_channel_id not configured",
            )
            return

        try:
            from src.thesis.drift_service import DriftService
            from src.thesis.review_service import ReviewService

            # ── Step 1: Detect drifted theses (no AI) ──
            async with AsyncSessionLocal() as session:
                drift_svc = DriftService(
                    session=session,
                    quote_service=get_quote_service(),
                    threshold_pct=settings.thesis_drift_threshold_pct,
                    cooldown_hours=settings.thesis_drift_cooldown_hours,
                )
                signals = await drift_svc.detect(str(user_id))

            if not signals:
                logger.debug("scheduler.drift.no_signals", user_id=user_id)
                await self._monitor.record_success(task_name)
                return

            logger.info(
                "scheduler.drift.signals_found",
                count=len(signals),
                tickers=[s.ticker for s in signals],
            )

            # ── Step 2: AI review per drifted thesis (sequential, rate-limit safe) ──
            reviews: list[tuple] = []
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

            if not reviews:
                return

            # ── Step 3: Discord notify ──
            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning("scheduler.drift.channel_not_found", channel_id=channel_id)
                return

            lines: list[str] = []
            for signal, review in reviews:
                verdict_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(
                    str(review.verdict).lower(), "⚪"
                )
                lines.append(
                    f"{verdict_icon} **{signal.ticker}** {signal.direction}{abs(signal.drift_pct):.1f}% "
                    f"drift → AI verdict: **{review.verdict}** "
                    f"(confidence {review.confidence:.0%})"
                )

            embed = discord.Embed(
                title="⚡ Thesis Drift Alert",
                description="\n".join(lines),
                color=0xFF6B35,
            )
            ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
            embed.set_footer(
                text=f"Drift ≥{settings.thesis_drift_threshold_pct:.0f}% detected lúc {ict_time}"
            )
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info("scheduler.drift.notified", reviewed=len(reviews))
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
    """Fire watchlist reminders via Discord based on investor-set frequency."""

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
        channel_id = getattr(settings, "morning_channel_id", None)
        if not channel_id:
            logger.warning("scheduler.reminder.skipped", label=label, reason="morning_channel_id not configured")
            return

        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            logger.warning("scheduler.reminder.channel_not_found", channel_id=channel_id)
            return

        frequency_map = {
            "daily":  [ReminderFrequency.DAILY],
            "weekly": [ReminderFrequency.WEEKLY],
        }
        frequencies = frequency_map.get(label, [ReminderFrequency.DAILY])

        try:
            async with AsyncSessionLocal() as session:
                svc = ReminderService(session)
                due = await svc.list_due(frequencies=frequencies)

                if not due:
                    logger.debug("scheduler.reminder.none_due", label=label)
                    await self._monitor.record_success(task_name)
                    return

                logger.info("scheduler.reminder.firing", label=label, count=len(due))

                now_utc = datetime.datetime.now(tz=datetime.UTC)
                ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
                freq_label = "hàng ngày" if label == "daily" else "hàng tuần"

                for reminder in due:
                    ticker = (
                        reminder.watchlist_item.ticker
                        if reminder.watchlist_item
                        else f"item#{reminder.watchlist_item_id}"
                    )
                    try:
                        embed = discord.Embed(
                            title=f"⏰ Nhắc nhở {freq_label}: {ticker}",
                            description=(
                                f"Bạn đang theo dõi **{ticker}** trong watchlist.\n"
                                f"Hãy kiểm tra lại thesis và diễn biến giá hôm nay."
                            ),
                            color=0x4F98A3,
                        )
                        embed.set_footer(text=f"Reminder {freq_label} — {ict_time}")
                        await channel.send(embed=embed)  # type: ignore[union-attr]
                        await svc.mark_sent(reminder)
                        logger.info(
                            "scheduler.reminder.sent",
                            ticker=ticker,
                            reminder_id=reminder.id,
                            frequency=reminder.frequency,
                        )
                    except Exception as exc:
                        logger.error(
                            "scheduler.reminder.send_failed",
                            ticker=ticker,
                            reminder_id=reminder.id,
                            error=str(exc),
                        )

                await session.commit()
            await self._monitor.record_success(task_name)

        except Exception as exc:
            logger.error("scheduler.reminder.error", label=label, error=str(exc))
            await self._monitor.record_failure(task_name, exc)


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

        # ── Step 1: Find decisions that reached their horizon ──
        try:
            async with AsyncSessionLocal() as session:
                svc = DecisionService(
                    session=session,
                    quote_service=get_quote_service(),
                    replay_agent=get_replay_agent(),
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

        # ── Step 2: Evaluate + Replay + Persist lesson per decision ──
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

        # ── Step 3: Discord notify ──
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            logger.warning("scheduler.decision_replay.channel_not_found", channel_id=channel_id)
            return

        verdict_icon = {"CORRECT": "✅", "INCORRECT": "❌", "MIXED": "🟡"}
        lines: list[str] = []
        for item in results:
            d = item["decision"]
            r = item["replay"]
            icon = verdict_icon.get(str(d.outcome_verdict).upper(), "⚪")
            pnl_str = f"{d.outcome_pnl_pct:+.1f}%" if d.outcome_pnl_pct is not None else "N/A"
            line = f"{icon} **{d.ticker}** {d.decision_type} → {d.outcome_verdict} ({pnl_str})"
            if r and r.key_lesson:
                line += f"\n    💡 _{r.key_lesson}_"
            if r and r.pattern_detected:
                line += f"\n    🔍 Pattern: `{r.pattern_detected}`"
            lines.append(line)

        ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
        embed = discord.Embed(
            title="🔄 Decision Replay — Kết quả sau horizon",
            description="\n\n".join(lines),
            color=0x4F98A3,
        )
        embed.set_footer(text=f"{len(results)} quyết định được đánh giá lúc {ict_time}")

        try:
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
# Aliases
# ---------------------------------------------------------------------------

Scheduler = BriefingScheduler
