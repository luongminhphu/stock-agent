"""In-memory cache for Daily Agenda summary strings + structured buckets.

Owner: briefing segment.
Single-process, single-user app today, but keyed by user_id for future safety.

Design:
- set_agenda(user_id, summary, buckets): store or clear a compact agenda summary
  string and optional structured buckets (decide/watch/defer).
- get_agenda(user_id): retrieve the last cached summary + buckets for that user.

No persistence: this is a lightweight, process-local helper used by
BriefingListener (scheduler path) and BriefingCog (manual slash commands)
so both entrypoints can surface the same Daily Agenda block without
rebuilding agenda via AI on every call.

Wave B.1 — AgendaBuckets struct:
  To support domain-level mapping DECIDE -> ACT_TODAY, we additionally cache
  structured buckets (tickers grouped into decide/watch/defer). BriefingService
  can consume these buckets to enforce that each DECIDE ticker receives at least
  one ACT_TODAY action in the brief, independent of LLM behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class AgendaBuckets:
    """Structured agenda buckets for a user.

    decide: tickers that should be acted on today.
    watch:  tickers to monitor closely.
    defer:  tickers explicitly pushed out of today's focus.

    Buckets are best-effort — when AgendaService or DailyAgendaCompletedEvent
    does not carry full information (e.g., no defer tickers yet), missing
    buckets simply stay empty.
    """

    decide: list[str]
    watch: list[str]
    defer: list[str]


@dataclass
class CachedAgenda:
    """Cached agenda payload for a user.

    summary: compact multi-line string used for Discord embed prefix.
    buckets: optional structured buckets for domain-level consumers.

    When summary is cleared (empty/None), the cache entry is removed entirely
    to avoid stale agendas; buckets are treated as best-effort metadata.
    """

    summary: str
    buckets: AgendaBuckets | None = None


_AgendaCache: Dict[str, CachedAgenda] = {}


def set_agenda(
    user_id: str,
    summary: str | None,
    buckets: AgendaBuckets | None = None,
    session_factory=None,
) -> None:
    """Set or clear the cached agenda for a user.

    Passing an empty/None summary clears the cache entry entirely, including
    any previously stored buckets. This keeps the cache small and avoids
    showing stale agendas when the scheduler runs with an empty agenda for
    a given day.

    session_factory (Wave D.1): when provided, also persists the agenda to DB
    so it survives restarts within the same calendar day.
    """
    if summary:
        _AgendaCache[user_id] = CachedAgenda(summary=summary, buckets=buckets)
        # Wave D.1: fire-and-forget persist to DB
        if session_factory is not None:
            import asyncio as _asyncio
            _asyncio.create_task(persist_agenda(session_factory, user_id, summary, buckets))
    else:
        _AgendaCache.pop(user_id, None)


def get_agenda(user_id: str) -> CachedAgenda | None:
    """Return the cached agenda (summary + buckets) for a user, if any."""
    return _AgendaCache.get(user_id)

# ---------------------------------------------------------------------------
# DB persistence helpers (Wave D.1)
# ---------------------------------------------------------------------------

async def persist_agenda(session_factory, user_id: str, summary: str, buckets: "AgendaBuckets | None") -> None:
    """Upsert today's agenda to DB. Fire-and-forget — never raises."""
    if session_factory is None or not summary:
        return
    try:
        import json as _json
        from datetime import UTC, date as _date, datetime as _dt
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from src.readmodel.models import DailyAgenda

        today = _date.today()
        buckets_json = None
        if buckets is not None:
            buckets_json = _json.dumps({
                "decide": buckets.decide,
                "watch": buckets.watch,
                "defer": buckets.defer,
            })

        async with session_factory() as session:
            stmt = pg_insert(DailyAgenda).values(
                user_id=user_id,
                agenda_date=today,
                summary=summary,
                buckets_json=buckets_json,
                created_at=_dt.now(UTC),
            ).on_conflict_do_update(
                constraint="uq_daily_agendas_user_date",
                set_={
                    "summary": summary,
                    "buckets_json": buckets_json,
                    "created_at": _dt.now(UTC),
                },
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "agenda_cache.persist_failed", extra={"user_id": user_id, "error": str(exc)}
        )


async def load_today_agendas_from_db(session_factory) -> dict[str, "CachedAgenda"]:
    """Load today's agendas from DB on startup. Returns {user_id: CachedAgenda}."""
    if session_factory is None:
        return {}
    try:
        import json as _json
        from datetime import date as _date
        from sqlalchemy import select
        from src.readmodel.models import DailyAgenda

        today = _date.today()
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(DailyAgenda).where(DailyAgenda.agenda_date == today)
                )
            ).scalars().all()
            result = {}
            for row in rows:
                buckets = None
                if row.buckets_json:
                    try:
                        data = _json.loads(row.buckets_json)
                        buckets = AgendaBuckets(
                            decide=data.get("decide", []),
                            watch=data.get("watch", []),
                            defer=data.get("defer", []),
                        )
                    except Exception:
                        pass
                result[row.user_id] = CachedAgenda(summary=row.summary, buckets=buckets)
            import logging
            logging.getLogger(__name__).info(
                "agenda_cache.loaded_from_db", extra={"count": len(result)}
            )
            return result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "agenda_cache.load_failed", extra={"error": str(exc)}
        )
        return {}


async def warm_load_agendas(session_factory) -> int:
    """Populate _AgendaCache from DB for today. Returns number of users loaded."""
    loaded = await load_today_agendas_from_db(session_factory)
    for user_id, cached in loaded.items():
        _AgendaCache[user_id] = cached
    return len(loaded)
