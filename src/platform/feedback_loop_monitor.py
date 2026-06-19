"""FeedbackLoopMonitor — sliding-window error-rate tracking for UserActionFeedbackListener.

Owner: platform segment (cross-cutting concern).

Purpose:
    Track success/failure counts per adapter (thesis, watchlist, memory,
    snapshot) with a 1-hour sliding window. When error rate exceeds the
    configured threshold (default 5%) AND the minimum sample count is met
    (default 10 events), fire a Discord alert.

Design:
    - Uses a deque of (timestamp, ok: bool) per adapter — O(1) append,
      O(n) window trim where n = events in the past hour (bounded in practice).
    - No DB, no external service — pure in-memory. Mirrors SchedulerMonitor.
    - Alert deduplication: once an alert fires for an adapter, no further
      alerts for that adapter until the rate drops below threshold and then
      rises again (edge-triggered, not level-triggered).
    - Thread/async safe via asyncio.Lock.

Usage::

    monitor = get_feedback_monitor()
    monitor.set_alert_channel(bot_channel)   # call after bot login

    # In each adapter:
    await monitor.record_ok("thesis")
    await monitor.record_error("thesis", error_message)

Config (env-driven via Settings, with safe defaults)::

    FEEDBACK_MONITOR_ERROR_THRESHOLD_PCT = 5    # alert when error_rate >= this
    FEEDBACK_MONITOR_MIN_SAMPLE           = 10  # skip alert if fewer events in window
    FEEDBACK_MONITOR_WINDOW_SECONDS       = 3600
"""
from __future__ import annotations

import asyncio
import datetime
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Deque

if TYPE_CHECKING:
    import discord

from src.platform.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants (can be overridden via Settings if needed later)
# ---------------------------------------------------------------------------

_ERROR_THRESHOLD_PCT: float = 5.0   # alert when error_rate >= 5%
_MIN_SAMPLE:         int   = 10     # ignore window if fewer events
_WINDOW_SECONDS:     int   = 3600   # 1-hour sliding window

# Adapter names (kept as constants to avoid typos in callers)
ADAPTER_THESIS    = "thesis"
ADAPTER_WATCHLIST = "watchlist"
ADAPTER_MEMORY    = "memory"
ADAPTER_SNAPSHOT  = "snapshot"

_ALL_ADAPTERS = (ADAPTER_THESIS, ADAPTER_WATCHLIST, ADAPTER_MEMORY, ADAPTER_SNAPSHOT)


# ---------------------------------------------------------------------------
# Internal dataclass
# ---------------------------------------------------------------------------

@dataclass
class _AdapterWindow:
    name: str
    events: Deque[tuple[datetime.datetime, bool]] = field(default_factory=deque)
    # Alert deduplication — True while alert is active (rate above threshold)
    alert_active: bool = False
    total_ok:    int = 0
    total_error: int = 0

    def trim(self, cutoff: datetime.datetime) -> None:
        """Remove events older than cutoff from the left of the deque."""
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def rate(self) -> tuple[int, int, float]:
        """Return (total, errors, error_rate_pct) within the current window."""
        total  = len(self.events)
        errors = sum(1 for _, ok in self.events if not ok)
        rate   = (errors / total * 100.0) if total > 0 else 0.0
        return total, errors, rate


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class FeedbackLoopMonitor:
    """Sliding-window error-rate monitor for the UserActionFeedbackListener."""

    def __init__(
        self,
        error_threshold_pct: float = _ERROR_THRESHOLD_PCT,
        min_sample:          int   = _MIN_SAMPLE,
        window_seconds:      int   = _WINDOW_SECONDS,
    ) -> None:
        self._threshold = error_threshold_pct
        self._min_sample = min_sample
        self._window = window_seconds
        self._adapters: dict[str, _AdapterWindow] = {
            name: _AdapterWindow(name=name) for name in _ALL_ADAPTERS
        }
        self._lock = asyncio.Lock()
        self._alert_channel: "discord.TextChannel | None" = None

    def set_alert_channel(self, channel: "discord.TextChannel") -> None:
        """Inject Discord channel after bot login."""
        self._alert_channel = channel
        logger.info(
            "feedback_loop_monitor.alert_channel_set",
            channel_id=channel.id,
        )

    # ------------------------------------------------------------------
    # Public record API
    # ------------------------------------------------------------------

    async def record_ok(self, adapter: str) -> None:
        """Record a successful adapter call."""
        now = datetime.datetime.now(tz=datetime.UTC)
        async with self._lock:
            w = self._get_window(adapter)
            w.total_ok += 1
            w.events.append((now, True))
            self._trim(w, now)
            # Recovery: if rate dropped below threshold, reset alert flag
            _, _, rate = w.rate()
            if w.alert_active and rate < self._threshold:
                w.alert_active = False
                logger.info(
                    "feedback_loop_monitor.rate_recovered",
                    adapter=adapter,
                    error_rate_pct=round(rate, 1),
                )

    async def record_error(self, adapter: str, error: str) -> None:
        """Record a failed adapter call and fire Discord alert if threshold crossed."""
        now = datetime.datetime.now(tz=datetime.UTC)
        should_alert = False
        rate_info: tuple[int, int, float] = (0, 0, 0.0)

        async with self._lock:
            w = self._get_window(adapter)
            w.total_error += 1
            w.events.append((now, False))
            self._trim(w, now)
            total, errors, rate = w.rate()
            rate_info = (total, errors, rate)

            if (
                total >= self._min_sample
                and rate >= self._threshold
                and not w.alert_active
            ):
                w.alert_active = True
                should_alert = True

        logger.warning(
            "feedback_loop_monitor.adapter_error",
            adapter=adapter,
            error=error,
            window_total=rate_info[0],
            window_errors=rate_info[1],
            error_rate_pct=round(rate_info[2], 1),
        )

        if should_alert:
            await self._send_alert(adapter, rate_info, error)

    # ------------------------------------------------------------------
    # Stats (for health embed / debug)
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, dict]:
        """Return current window stats for all adapters. Not async — read-only snapshot."""
        now = datetime.datetime.now(tz=datetime.UTC)
        out: dict[str, dict] = {}
        for name, w in self._adapters.items():
            w.trim(now - datetime.timedelta(seconds=self._window))
            total, errors, rate = w.rate()
            out[name] = {
                "window_total":   total,
                "window_errors":  errors,
                "error_rate_pct": round(rate, 1),
                "total_ok":       w.total_ok,
                "total_error":    w.total_error,
                "alert_active":   w.alert_active,
            }
        return out

    def get_health_embed(self) -> "discord.Embed":
        """Build a Discord embed summarising current error rates."""
        import discord  # noqa: PLC0415

        stats  = self.get_stats()
        any_alert = any(v["alert_active"] for v in stats.values())
        color  = 0xE74C3C if any_alert else 0x2ECC71

        embed = discord.Embed(
            title="🔁 Feedback Loop Monitor",
            color=color,
        )
        lines: list[str] = []
        for adapter, s in stats.items():
            icon = "🔴" if s["alert_active"] else ("🟡" if s["error_rate_pct"] > 0 else "🟢")
            lines.append(
                f"{icon} `{adapter}` — {s['error_rate_pct']}% lỗi "
                f"({s['window_errors']}/{s['window_total']} trong 1h) "
                f"| tổng: {s['total_ok']} ok / {s['total_error']} lỗi"
            )
        embed.description = "\n".join(lines) or "Chưa có dữ liệu."

        ict_now = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=7)
        embed.set_footer(
            text=f"Ngưỡng cảnh báo: {self._threshold}% (min {self._min_sample} events/1h) — "
                 f"{ict_now.strftime('%d/%m %H:%M ICT')}"
        )
        return embed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_window(self, adapter: str) -> _AdapterWindow:
        if adapter not in self._adapters:
            self._adapters[adapter] = _AdapterWindow(name=adapter)
        return self._adapters[adapter]

    def _trim(self, w: _AdapterWindow, now: datetime.datetime) -> None:
        cutoff = now - datetime.timedelta(seconds=self._window)
        w.trim(cutoff)

    async def _send_alert(
        self,
        adapter:   str,
        rate_info: tuple[int, int, float],
        last_error: str,
    ) -> None:
        total, errors, rate = rate_info
        logger.error(
            "feedback_loop_monitor.threshold_exceeded",
            adapter=adapter,
            error_rate_pct=round(rate, 1),
            window_total=total,
            window_errors=errors,
            last_error=last_error,
        )

        if self._alert_channel is None:
            logger.warning(
                "feedback_loop_monitor.no_alert_channel",
                hint="set_alert_channel() chưa được gọi sau khi bot login",
            )
            return

        try:
            import discord  # noqa: PLC0415

            ict_now = (
                datetime.datetime.now(tz=datetime.UTC)
                + datetime.timedelta(hours=7)
            )
            embed = discord.Embed(
                title="🚨 Feedback Loop — Tỷ lệ lỗi cao",
                description=(
                    f"Adapter **`{adapter}`** đang có tỷ lệ lỗi "
                    f"**{round(rate, 1)}%** trong 1 giờ qua "
                    f"({errors}/{total} events).\n\n"
                    f"**Ngưỡng cảnh báo:** {self._threshold}%\n"
                    f"**Lỗi gần nhất:**\n```{last_error[:400]}```"
                ),
                color=0xE74C3C,
            )
            embed.add_field(
                name="⚠️ Ảnh hưởng",
                value=(
                    "Feedback loop có thể đang bị gián đoạn — "
                    "hành động mua/bán của nhà đầu tư có thể không được "
                    "ghi nhận đúng vào thesis/watchlist."
                ),
                inline=False,
            )
            embed.add_field(
                name="🔧 Hành động đề xuất",
                value=(
                    "1. Kiểm tra logs: `docker compose logs api --tail 100`\n"
                    "2. Kiểm tra DB connectivity\n"
                    "3. Dùng `/health` để xem trạng thái tổng thể"
                ),
                inline=False,
            )
            embed.set_footer(
                text=f"Alert lúc {ict_now.strftime('%d/%m %H:%M ICT')} — stock-agent feedback-loop-monitor"
            )
            await self._alert_channel.send(embed=embed)
            logger.info(
                "feedback_loop_monitor.alert_sent",
                adapter=adapter,
                channel_id=self._alert_channel.id,
            )
        except Exception as exc:
            logger.error("feedback_loop_monitor.alert_send_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_monitor: FeedbackLoopMonitor | None = None


def get_feedback_monitor() -> FeedbackLoopMonitor:
    """Return the process-level FeedbackLoopMonitor singleton."""
    global _monitor
    if _monitor is None:
        _monitor = FeedbackLoopMonitor()
    return _monitor
