"""Scheduler — orchestrate recurring bot tasks.

Owner: bot segment (adapter only).
No business logic — calls domain services on schedule.

Registered tasks:
    BriefingScheduler.morning_brief_task       — weekdays 08:45 ICT
    BriefingScheduler.eod_brief_task           — weekdays 15:05 ICT
    WatchlistScanScheduler.scan_task           — every 5 min, weekdays 09:00–15:00 ICT
    ThesisMaintenanceScheduler.maintenance     — weekdays 08:30 ICT (before morning brief)

Note:
    MORNING_CHANNEL_ID and EOD_CHANNEL_ID must be set in settings.
    SCHEDULER_USER_ID is the service account used for scheduled tasks.
"""

from __future__ import annotations

import datetime

import discord
from discord.ext import tasks

from src.bot.commands.briefing import build_brief_embed
from src.briefing.service import BriefingService
from src.platform.bootstrap import get_briefing_agent, get_quote_service, get_thesis_review_agent
from src.platform.config import settings
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# BriefingScheduler
# ---------------------------------------------------------------------------

_MORNING_TIME = datetime.time(hour=1, minute=45, tzinfo=datetime.UTC)  # 08:45 ICT
_EOD_TIME = datetime.time(hour=8, minute=5, tzinfo=datetime.UTC)       # 15:05 ICT


class BriefingScheduler:
    """Attach to a discord.Client after login."""

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    def start(self) -> None:
        self._morning_task.start()
        self._eod_task.start()
        logger.info("scheduler.briefing.started")

    def stop(self) -> None:
        self._morning_task.cancel()
        self._eod_task.cancel()
        logger.info("scheduler.briefing.stopped")

    @tasks.loop(time=_MORNING_TIME)
    async def _morning_task(self) -> None:
        await self._send_brief(phase="morning")

    @tasks.loop(time=_EOD_TIME)
    async def _eod_task(self) -> None:
        await self._send_brief(phase="eod")

    async def _send_brief(self, phase: str) -> None:
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

        except Exception as exc:
            logger.error("scheduler.briefing.error", phase=phase, error=str(exc))


# ---------------------------------------------------------------------------
# WatchlistScanScheduler
# ---------------------------------------------------------------------------

_SCAN_INTERVAL_MINUTES = 5
_MARKET_OPEN_UTC = datetime.time(hour=2, minute=0)   # 09:00 ICT
_MARKET_CLOSE_UTC = datetime.time(hour=8, minute=0)  # 15:00 ICT


class WatchlistScanScheduler:
    """Scan watchlist every 5 minutes during market hours and notify Discord.

    - Runs weekdays 09:00–15:00 ICT only (silent outside market hours).
    - Sends embed only when signals exist (alert_triggered or strong_move).
    - Does NOT call AI — zero token cost.
    - Reuses morning_channel_id + scheduler_user_id from settings.
    """

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    def start(self) -> None:
        self._scan_task.start()
        logger.info("scheduler.scan.started", interval_minutes=_SCAN_INTERVAL_MINUTES)

    def stop(self) -> None:
        self._scan_task.cancel()
        logger.info("scheduler.scan.stopped")

    @tasks.loop(minutes=_SCAN_INTERVAL_MINUTES)
    async def _scan_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:
            return
        now_time = now_utc.time().replace(tzinfo=datetime.UTC)
        if not (_MARKET_OPEN_UTC <= now_time.replace(tzinfo=None) <= _MARKET_CLOSE_UTC):
            return

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

            if not result.signals:
                return

            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                logger.warning("scheduler.scan.channel_not_found", channel_id=channel_id)
                return

            lines: list[str] = []
            for s in result.signals:
                icon = "🔔" if s.has_alerts else "📊"
                lines.append(f"{icon} **{s.ticker}** {s.change_pct:+.1f}% — {s.description}")

            has_triggered = result.triggered_count > 0
            embed = discord.Embed(
                title="📡 Watchlist Scan",
                description="\n".join(lines),
                color=0xFF6B35 if has_triggered else 0x4F98A3,
            )
            ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
            embed.set_footer(text=f"Scan lúc {ict_time} — {len(result.signals)} tín hiệu")

            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "scheduler.scan.notified",
                signals=len(result.signals),
                triggered=result.triggered_count,
            )

        except Exception as exc:
            logger.error("scheduler.scan.error", error=str(exc))

    @_scan_task.before_loop
    async def _before_scan(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# ThesisMaintenanceScheduler  (Wave 5)
# ---------------------------------------------------------------------------

_MAINTENANCE_TIME = datetime.time(hour=1, minute=30, tzinfo=datetime.UTC)  # 08:30 ICT
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

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    def start(self) -> None:
        self._maintenance_task.start()
        logger.info("scheduler.thesis_maintenance.started")

    def stop(self) -> None:
        self._maintenance_task.cancel()
        logger.info("scheduler.thesis_maintenance.stopped")

    @tasks.loop(time=_MAINTENANCE_TIME)
    async def _maintenance_task(self) -> None:
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        if now_utc.weekday() >= 5:  # Skip weekends
            return

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

        # ── Step 3: Discord notify if anything changed ──
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
            lines: list[str] = []
            if expired_count:
                lines.append(f"⏰ **{expired_count}** catalyst đã hết hạn → EXPIRED")
            for r in reviews:
                verdict_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(
                    str(r.verdict).lower(), "⚪"
                )
                lines.append(
                    f"{verdict_icon} Thesis #{r.thesis_id} — {r.verdict} "
                    f"(confidence: {r.confidence:.0%})"
                )

            embed = discord.Embed(
                title="🔧 Thesis Maintenance",
                description="\n".join(lines),
                color=0x4F98A3,
            )
            ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
            embed.set_footer(text=f"Auto-maintenance lúc {ict_time}")
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info("scheduler.thesis_maintenance.notified")
        except Exception as exc:
            logger.error("scheduler.thesis_maintenance.notify_error", error=str(exc))

    @_maintenance_task.before_loop
    async def _before_maintenance(self) -> None:
        await self._client.wait_until_ready()


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

Scheduler = BriefingScheduler
