"""In-memory cache for Daily Agenda summary strings.

Owner: briefing segment.
Single-process, single-user app today, but keyed by user_id for future safety.

Design:
- set_agenda(user_id, summary): store or clear a compact agenda summary string.
- get_agenda(user_id): retrieve the last cached summary for that user, if any.

No persistence: this is a lightweight, process-local helper used by
BriefingListener (scheduler path) and BriefingCog (manual slash commands)
so both entrypoints can surface the same Daily Agenda block without
rebuilding agenda via AI on every call.
"""
from __future__ import annotations

from typing import Dict

_AgendaCache: Dict[str, str] = {}


def set_agenda(user_id: str, summary: str | None) -> None:
    """Set or clear the cached agenda summary for a user.

    Passing an empty/None summary clears the cache entry. This keeps the
    cache small and avoids showing stale agendas when the scheduler runs
    with an empty agenda for a given day.
    """
    if summary:
        _AgendaCache[user_id] = summary
    else:
        _AgendaCache.pop(user_id, None)


def get_agenda(user_id: str) -> str | None:
    """Return the cached agenda summary for a user, if any."""
    return _AgendaCache.get(user_id)
