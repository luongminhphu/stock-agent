"""Scheduler — orchestrate recurring bot tasks.

Owner: bot segment (adapter only).
No business logic — calls domain services on schedule.

Registered tasks:
    morning_brief_task  — weekdays 08:45 ICT, send brief to configured channel
    eod_brief_task      — weekdays 15:05 ICT, send EOD brief after market close

Extend by adding more tasks that call domain services.

Note:
    MORNING_CHANNEL_ID and EOD_CHANNEL_ID must be set in settings.
    user_id for scheduled briefs uses SCHEDULER_USER_ID from settings
    (a service account / admin user that has a populated watchlist).
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

# Weekdays only, ICT = UTC+7
_MORNING_TIME = datetime.time(hour=1, minute=45, tzinfo=datetime.timezone.utc)  # 08:45 ICT
_EOD_TIME = datetime.time(hour=8, minute=5, tzinfo=datetime.timezone.utc)  # 15:05 ICT


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


# Alias — src/bot/__init__.py imports `Scheduler` by this name
Scheduler = BriefingScheduler
