"""Scheduler — orchestrate recurring bot tasks.

Owner: bot segment (adapter only).
No business logic — calls domain services on schedule.

Registered tasks:
    BriefingScheduler.morning_brief_task  — weekdays 08:45 ICT
    BriefingScheduler.eod_brief_task      — weekdays 15:05 ICT
    WatchlistScanScheduler.scan_task      — every 5 min, weekdays 09:00–15:00 ICT

Note:
    MORNING_CHANNEL_ID and EOD_CHANNEL_ID must be set in settings.
    SCHEDULER_USER_ID is the service account used for scheduled tasks.
"""

from __future__ import annotations

import datetime

import discord
from discord.ext import tasks

from src.bot.commands.briefing import _build_brief_embed
from src.briefing.service import BriefingService
from src.platform.bootstrap import get_briefing_agent, get_quote_service
from src.platform.config import settings
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# BriefingScheduler
# ---------------------------------------------------------------------------

# Weekdays only, ICT = UTC+7
_MORNING_TIME = datetime.time(hour=1, minute=45, tzinfo=datetime.UTC)  # 08:45 ICT
_EOD_TIME = datetime.time(hour=8, minute=5, tzinfo=datetime.UTC)  # 15:05 ICT


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

    async def _send_brief(
        self,
        phase: str,
    ) -> None:
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

            embed = _build_brief_embed(brief, phase=phase)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info("scheduler.briefing.sent", phase=phase, channel_id=channel_id)

        except Exception as exc:
            logger.error("scheduler.briefing.error", phase=phase, error=str(exc))


# ---------------------------------------------------------------------------
# WatchlistScanScheduler
# ---------------------------------------------------------------------------

_SCAN_INTERVAL_MINUTES = 5

# Market hours ICT (UTC+7) expressed in UTC
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
        # Skip weekends
        if now_utc.weekday() >= 5:
            return
        # Skip outside market hours
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
# Aliases
# ---------------------------------------------------------------------------

# src/bot/__init__.py imports `Scheduler` by this name
Scheduler = BriefingScheduler
