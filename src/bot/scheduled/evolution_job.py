"""
Evolution scheduled job — bot segment.

Runs weekly (Monday 06:00 ICT) to trigger SelfImprovementAdvisor
and emit EvolutionSuggestionReadyEvent.

Owner: bot segment (thin scheduler wrapper).
Domain logic lives in: src/core/evolution.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

from src.platform.event_bus import get_event_bus
from src.platform.events import EvolutionSuggestionReadyEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

_ICT = timezone(timedelta(hours=7))


class EvolutionScheduler:
    """Weekly job: analyse feedback → suggest improvements → emit event.

    Fires once per week on Monday at 06:00 ICT.
    Guardrail: never auto-applies any suggestion.
    Owner must review via Discord embed or API before marking applied.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self._bot = bot
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._loop())
        logger.info("evolution_scheduler.started")

    async def _loop(self) -> None:
        await self._bot.wait_until_ready()
        while not self._bot.is_closed():
            try:
                await self._wait_until_next_run()
                await self._run()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("evolution_scheduler.loop_error", error=str(exc))
                await asyncio.sleep(60)

    async def _wait_until_next_run(self) -> None:
        """Sleep until next Monday 06:00 ICT."""
        now = datetime.now(_ICT)
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_run = now.replace(
            hour=6, minute=0, second=0, microsecond=0
        ) + timedelta(days=days_until_monday if now.weekday() != 0 or now.hour >= 6 else 0)
        if now.weekday() == 0 and now.hour < 6:
            next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
        delay = (next_run - now).total_seconds()
        logger.info(
            "evolution_scheduler.next_run",
            next_run=next_run.isoformat(),
            delay_hours=round(delay / 3600, 1),
        )
        await asyncio.sleep(max(delay, 1))

    async def _run(self) -> None:
        """Core: run advisor → emit event if suggestions found."""
        from src.ai.client import get_ai_client
        from src.core.evolution import SelfImprovementAdvisor

        logger.info("evolution_scheduler.run_start")
        try:
            ai_client = get_ai_client()
        except Exception:
            ai_client = None
            logger.warning("evolution_scheduler.ai_client_unavailable", fallback="heuristic")

        advisor = SelfImprovementAdvisor(ai_client=ai_client)
        try:
            suggestions = await advisor.analyse_and_suggest(days=30)
        except Exception as exc:
            logger.exception("evolution_scheduler.advisor_failed", error=str(exc))
            return

        if not suggestions:
            logger.info("evolution_scheduler.no_suggestions")
            return

        # Derive overall_accuracy from first heuristic pass — PatternReport not
        # re-exposed here, so we default to 0.0 and let subscriber display count.
        has_high_risk = any(
            getattr(s, "risk_level", "low") == "high" for s in suggestions
        )

        event = EvolutionSuggestionReadyEvent(
            suggestion_count=len(suggestions),
            overall_accuracy=0.0,   # PatternReport not re-fetched here; accuracy shown in embed
            has_high_risk=has_high_risk,
            period_days=30,
        )
        bus = get_event_bus()
        await bus.publish(event)
        logger.info(
            "evolution_scheduler.event_emitted",
            suggestion_count=len(suggestions),
            has_high_risk=has_high_risk,
        )
