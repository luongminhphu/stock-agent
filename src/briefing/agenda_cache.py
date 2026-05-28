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


def set_agenda(user_id: str, summary: str | None, buckets: AgendaBuckets | None = None) -> None:
    """Set or clear the cached agenda for a user.

    Passing an empty/None summary clears the cache entry entirely, including
    any previously stored buckets. This keeps the cache small and avoids
    showing stale agendas when the scheduler runs with an empty agenda for
    a given day.
    """
    if summary:
        _AgendaCache[user_id] = CachedAgenda(summary=summary, buckets=buckets)
    else:
        _AgendaCache.pop(user_id, None)


def get_agenda(user_id: str) -> CachedAgenda | None:
    """Return the cached agenda (summary + buckets) for a user, if any."""
    return _AgendaCache.get(user_id)
