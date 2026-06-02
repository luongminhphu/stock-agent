from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.scheduler_monitor import get_monitor


SignalSource = Literal[
    "THESIS_DRIFT",
    "WATCHLIST_SCAN",
    "PROACTIVE",
    "REMINDER",
    "INTELLIGENCE",
    "BRIEF",
    "OTHER",
]

SignalSeverity = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass
class TodaySignal:
    id: str
    ticker: str
    source: SignalSource
    severity: SignalSeverity
    created_at: datetime
    headline: str
    details: str | None
    action_hint: str | None
    link_type: str | None
    link_target: str | None
    tags: list[str]


@dataclass
class EngineStatus:
    last_run: datetime | None
    ok: bool
    consecutive_failures: int = 0


@dataclass
class TodayLoopSummary:
    total_signals: int
    by_source: dict[str, int]


@dataclass
class TodayLoopResult:
    date: date
    generated_at: datetime
    summary: TodayLoopSummary
    top_actions: list[TodaySignal]
    signals: list[TodaySignal]
    engine_status: dict[str, EngineStatus]


class TodayLoopQueryService:
    """Aggregate today's signals and scheduler health into a single view.

    Wave 1 scope:
        - Start with scheduler health only.
        - Signals list is empty until underlying segments expose
          drift/scan/reminder readmodels.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._monitor = get_monitor()

    async def get_today_loop(self, user_id: str) -> TodayLoopResult:
        # TODO Wave 1.5+: hydrate signals from drift/scan/reminder readmodels.
        today = date.today()
        now = datetime.now()

        signals: list[TodaySignal] = []
        summary = TodayLoopSummary(total_signals=0, by_source={})

        engine_status: dict[str, EngineStatus] = {}
        for task in [
            "briefing.morning",
            "intelligence_engine.morning",
            "proactive_watch.morning",
            "reminder.daily",
        ]:
            stats = self._monitor.get_task_stats(task)
            engine_status[task.replace(".", "_")] = EngineStatus(
                last_run=stats.last_run,
                ok=stats.consecutive_failures == 0,
                consecutive_failures=stats.consecutive_failures,
            )

        return TodayLoopResult(
            date=today,
            generated_at=now,
            summary=summary,
            top_actions=[],
            signals=signals,
            engine_status=engine_status,
        )
