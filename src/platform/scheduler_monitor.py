"""SchedulerMonitor — in-memory health tracking for all scheduled tasks.

Owner: platform segment (cross-cutting concern).

Responsibilities:
    - Track last_success_at / last_failure_at / consecutive_failures per task.
    - Fire a Discord alert when consecutive_failures reaches the threshold.
    - Provide get_health_embed() for the /health slash command.

Usage in scheduler.py:
    from src.platform.scheduler_monitor import get_monitor
    monitor = get_monitor()
    monitor.register_task("briefing.morning")   # call in start()
    ...
    await monitor.record_success("briefing.morning")
    await monitor.record_failure("briefing.morning", exc)

Notes:
    - Pure in-memory — state is lost on restart. This is intentional;
      we want the monitor to reflect runtime health, not historical data.
    - Thread/async safe via asyncio.Lock.
    - No DB dependency, no domain imports.
"""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

from src.platform.logging import get_logger

logger = get_logger(__name__)

_FAILURE_THRESHOLD = 3  # consecutive failures before Discord alert


@dataclass
class TaskStatus:
    task_name: str
    last_success_at: datetime.datetime | None = None
    last_failure_at: datetime.datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    total_runs: int = 0
    total_failures: int = 0

    @property
    def is_healthy(self) -> bool:
        return self.consecutive_failures < _FAILURE_THRESHOLD

    @property
    def status_icon(self) -> str:
        if self.consecutive_failures == 0:
            return "🟢"
        if self.consecutive_failures < _FAILURE_THRESHOLD:
            return "🟡"
        return "🔴"

    @property
    def last_run_str(self) -> str:
        """Human-readable last run time (ICT)."""
        ts = self.last_success_at or self.last_failure_at
        if ts is None:
            return "chưa chạy"
        ict = ts + datetime.timedelta(hours=7)
        return ict.strftime("%d/%m %H:%M ICT")


class SchedulerMonitor:
    """Singleton monitor — injected into scheduler classes."""

    def __init__(self, failure_threshold: int = _FAILURE_THRESHOLD) -> None:
        self._threshold = failure_threshold
        self._statuses: dict[str, TaskStatus] = {}
        self._lock = asyncio.Lock()
        # Optional Discord channel for proactive alerts (set after bot login).
        self._alert_channel: "discord.TextChannel | None" = None

    def set_alert_channel(self, channel: "discord.TextChannel") -> None:
        """Call after bot is ready to enable proactive Discord alerts."""
        self._alert_channel = channel
        logger.info("scheduler_monitor.alert_channel_set", channel_id=channel.id)

    def register_task(self, task_name: str) -> None:
        """Register a task at scheduler.start() time.

        Ensures /health always lists all known tasks — even before the first
        execution tick. Tasks registered here show 'chưa chạy' until
        record_success or record_failure is called.
        """
        if task_name not in self._statuses:
            self._statuses[task_name] = TaskStatus(task_name=task_name)
            logger.debug("scheduler_monitor.task_registered", task=task_name)

    def _get_or_create(self, task_name: str) -> TaskStatus:
        if task_name not in self._statuses:
            self._statuses[task_name] = TaskStatus(task_name=task_name)
        return self._statuses[task_name]

    async def record_success(self, task_name: str) -> None:
        async with self._lock:
            s = self._get_or_create(task_name)
            s.last_success_at = datetime.datetime.now(tz=datetime.UTC)
            s.consecutive_failures = 0
            s.total_runs += 1
        logger.debug("scheduler_monitor.success", task=task_name)

    async def record_failure(self, task_name: str, error: Exception | str) -> None:
        error_str = str(error)
        should_alert = False
        async with self._lock:
            s = self._get_or_create(task_name)
            s.last_failure_at = datetime.datetime.now(tz=datetime.UTC)
            s.last_error = error_str
            s.consecutive_failures += 1
            s.total_runs += 1
            s.total_failures += 1
            if s.consecutive_failures >= self._threshold:
                should_alert = True
        logger.warning(
            "scheduler_monitor.failure",
            task=task_name,
            consecutive=self._statuses[task_name].consecutive_failures,
            error=error_str,
        )
        if should_alert:
            await self._send_alert(task_name, self._statuses[task_name])

    async def _send_alert(self, task_name: str, status: TaskStatus) -> None:
        logger.error(
            "scheduler_monitor.consecutive_failure_threshold_reached",
            task=task_name,
            consecutive_failures=status.consecutive_failures,
            last_error=status.last_error,
        )
        if self._alert_channel is None:
            return
        try:
            import discord

            embed = discord.Embed(
                title="🚨 Scheduler Alert",
                description=(
                    f"Task **`{task_name}`** đã fail **{status.consecutive_failures} lần liên tiếp**.\n"
                    f"```{status.last_error}```"
                ),
                color=0xE74C3C,
            )
            ict_now = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=7)
            embed.set_footer(text=f"Alert lúc {ict_now.strftime('%d/%m %H:%M ICT')}")
            await self._alert_channel.send(embed=embed)
        except Exception as exc:
            logger.error("scheduler_monitor.alert_send_failed", error=str(exc))

    def get_status(self) -> dict[str, TaskStatus]:
        return dict(self._statuses)

    def get_health_embed(self) -> "discord.Embed":
        import discord

        statuses = list(self._statuses.values())
        all_healthy = all(s.is_healthy for s in statuses)
        color = 0x2ECC71 if all_healthy else 0xE74C3C

        embed = discord.Embed(
            title="🩺 Scheduler Health",
            color=color,
        )

        if not statuses:
            embed.description = "Chưa có task nào được ghi nhận. Scheduler có thể chưa chạy."
            return embed

        lines: list[str] = []
        for s in sorted(statuses, key=lambda x: x.task_name):
            fail_info = (
                f" — {s.consecutive_failures} lần fail liên tiếp" if s.consecutive_failures > 0 else ""
            )
            lines.append(
                f"{s.status_icon} `{s.task_name}` — last run: {s.last_run_str}{fail_info}"
            )

        embed.description = "\n".join(lines)

        unhealthy = [s for s in statuses if not s.is_healthy]
        if unhealthy:
            detail_lines = []
            for s in unhealthy:
                detail_lines.append(
                    f"**`{s.task_name}`**: {s.last_error or 'unknown error'}"
                )
            embed.add_field(
                name="⚠️ Lỗi gần nhất",
                value="\n".join(detail_lines),
                inline=False,
            )

        healthy_count = sum(1 for s in statuses if s.is_healthy)
        ict_now = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=7)
        embed.set_footer(
            text=f"{healthy_count}/{len(statuses)} tasks healthy — {ict_now.strftime('%d/%m %H:%M ICT')}"
        )
        return embed


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_monitor: SchedulerMonitor | None = None


def get_monitor() -> SchedulerMonitor:
    """Return the module-level SchedulerMonitor singleton.

    Created on first call; subsequent calls return the same instance.
    This pattern mirrors get_quote_service() / get_replay_agent() in bootstrap.py.
    """
    global _monitor
    if _monitor is None:
        _monitor = SchedulerMonitor()
    return _monitor
