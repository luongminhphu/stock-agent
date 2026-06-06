"""
Intelligence Snapshot Store — readmodel cache for IntelligenceReport.

Owner: readmodel segment.

Purpose:
    After core/engine.py produces an IntelligenceReport (via multi-agent
    orchestration or heuristic fallback), downstream consumers (bot, api)
    should NOT re-trigger the AI cycle on every query. This store provides
    a fast read path:

        bot:  snapshot = await store.get(user_id)  # returns latest cached report
        api:  GET /intelligence -> reads from here, never calls engine directly

Design:
    Two-layer cache:
    1. Hot layer   — DashboardTTLCache, TTL=300s. Fast in-process dict lookup.
                     Evicted automatically on expiry or explicit invalidation.
    2. Warm layer  — _long_term dict, no TTL. Holds the last known report
                     indefinitely so stale-while-revalidate is possible:
                     if hot layer miss, return warm layer + flag as stale.

    This means:
    - get() always returns something after the first cycle (no empty-hand
      responses to user once a report has been generated).
    - is_stale flag tells the caller to schedule a background refresh.

Usage::

    from src.readmodel.intelligence_snapshot import get_intelligence_snapshot

    store = get_intelligence_snapshot()

    # After engine.run_cycle() — called by core/engine.py or scheduler:
    await store.upsert(user_id, engine_output.intelligence_report)

    # In bot / api handler:
    result = await store.get(user_id)
    if result is None:
        # No report yet — engine hasn't run for this user
        return None
    report, is_stale = result
    if is_stale:
        # Optionally: trigger background refresh via scheduler
        pass

Invalidation::

    store.invalidate(user_id)       # evict hot layer only (warm remains)
    store.invalidate_all()          # nuclear — clears both layers
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.readmodel.cache import DashboardTTLCache

# ---------------------------------------------------------------------------
# DB persistence helpers (Wave D.1)
# ---------------------------------------------------------------------------

async def _persist_intelligence_snapshot(session_factory, user_id: str, report, trigger_source: str) -> None:
    """Upsert an IntelligenceSnapshot row. Fire-and-forget — never raises."""
    if session_factory is None:
        return
    try:
        import json as _json
        from datetime import UTC, datetime as _dt
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from src.readmodel.models import IntelligenceSnapshot

        try:
            report_json = _json.dumps(report.model_dump(), default=str)
        except Exception:
            report_json = _json.dumps({}, default=str)

        async with session_factory() as session:
            stmt = pg_insert(IntelligenceSnapshot).values(
                user_id=user_id,
                report_json=report_json,
                trigger_source=trigger_source or "unknown",
                captured_at=_dt.now(UTC),
            ).on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "report_json": report_json,
                    "trigger_source": trigger_source or "unknown",
                    "captured_at": _dt.now(UTC),
                },
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as exc:
        from src.platform.logging import get_logger as _get_logger
        _get_logger(__name__).warning(
            "intelligence_snapshot_store.persist_failed", user_id=user_id, error=str(exc)
        )


async def load_intelligence_snapshots_from_db(session_factory) -> dict[str, dict]:
    """Load persisted intelligence snapshots on startup. Returns {user_id: row_dict}."""
    if session_factory is None:
        return {}
    try:
        import json as _json
        from sqlalchemy import select
        from src.readmodel.models import IntelligenceSnapshot
        from src.platform.logging import get_logger as _get_logger

        async with session_factory() as session:
            rows = (await session.execute(select(IntelligenceSnapshot))).scalars().all()
            result = {}
            for row in rows:
                try:
                    result[row.user_id] = {
                        "report_json": row.report_json,
                        "trigger_source": row.trigger_source,
                        "captured_at": row.captured_at,
                    }
                except Exception:
                    pass
            _get_logger(__name__).info(
                "intelligence_snapshot_store.loaded_from_db", count=len(result)
            )
            return result
    except Exception as exc:
        from src.platform.logging import get_logger as _get_logger
        _get_logger(__name__).warning(
            "intelligence_snapshot_store.load_failed", error=str(exc)
        )
        return {}

if TYPE_CHECKING:
    from src.ai.schemas import IntelligenceReport

_NAMESPACE = "intelligence_report"
_HOT_TTL_SECONDS = 300  # 5 min hot cache


class IntelligenceSnapshotStore:
    """Readmodel cache for the latest IntelligenceReport per user.

    Lifecycle:
        upsert()  — called by engine after every run_cycle()
        get()     — called by bot/api to read latest report
        invalidate() — called on explicit refresh requests
    """

    def __init__(self, cache: DashboardTTLCache | None = None, session_factory=None) -> None:
        self._cache = cache or DashboardTTLCache()
        # warm layer: user_id -> (report, stored_at)
        self._warm: dict[str, tuple[IntelligenceReport, datetime]] = {}
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Write path — called by core/engine.py after run_cycle()
    # ------------------------------------------------------------------

    async def upsert(
        self,
        user_id: str,
        report: IntelligenceReport,
    ) -> None:
        """Store a fresh IntelligenceReport for user_id.

        Updates both hot (TTL) and warm (indefinite) layers.
        """
        self._cache.set(
            _NAMESPACE,
            user_id,
            report,
            ttl=_HOT_TTL_SECONDS,
        )
        self._warm[user_id] = (report, datetime.now(UTC))
        # Wave D.1: fire-and-forget persist to DB
        import asyncio as _asyncio
        _asyncio.create_task(
            _persist_intelligence_snapshot(
                self._session_factory, user_id, report,
                getattr(report, "trigger_source", "unknown"),
            )
        )

    # ------------------------------------------------------------------
    # Read path — called by bot/api
    # ------------------------------------------------------------------

    async def get(
        self,
        user_id: str,
    ) -> tuple[IntelligenceReport, bool] | None:
        """Return (report, is_stale) or None if no report exists yet.

        is_stale=False  — hot cache hit (report is fresh, <= 300s old)
        is_stale=True   — hot cache miss but warm layer has a previous
                          report; caller should schedule a background
                          refresh but can still render the stale data.
        Returns None only if no report has ever been generated for user.
        """
        hot = self._cache.get(_NAMESPACE, user_id)
        if hot is not None:
            return hot, False

        warm_entry = self._warm.get(user_id)
        if warm_entry is not None:
            report, _ = warm_entry
            return report, True

        return None

    async def get_or_none(
        self,
        user_id: str,
    ) -> IntelligenceReport | None:
        """Convenience: return report regardless of staleness, or None."""
        result = await self.get(user_id)
        if result is None:
            return None
        report, _ = result
        return report

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def last_updated_at(self, user_id: str) -> datetime | None:
        """Return the timestamp when the report was last upserted, or None."""
        entry = self._warm.get(user_id)
        if entry is None:
            return None
        _, stored_at = entry
        return stored_at

    def is_stale(self, user_id: str) -> bool | None:
        """True if warm entry exists but hot cache has expired. None if no entry."""
        if user_id not in self._warm:
            return None
        hot = self._cache.get(_NAMESPACE, user_id)
        return hot is None

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(self, user_id: str) -> None:
        """Evict hot layer for user. Warm layer is preserved for stale reads."""
        self._cache.invalidate(_NAMESPACE, user_id)

    def invalidate_all(self) -> None:
        """Clear both layers entirely."""
        self._cache.invalidate_all()
        self._warm.clear()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def active_users(self) -> list[str]:
        """Return user_ids that have at least one warm-layer entry."""
        return list(self._warm.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"IntelligenceSnapshotStore("
            f"warm_users={len(self._warm)}, "
            f"hot_alive={self._cache.alive_size()})"
        )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

    async def warm_load(self) -> int:
        """Load persisted reports from DB into warm layer on startup.

        Returns number of users loaded.
        Prevents the 204 cold-start problem where dashboard intelligence panel
        returns empty after restart until the next engine cycle runs.
        """
        rows = await load_intelligence_snapshots_from_db(self._session_factory)
        loaded = 0
        for user_id, row_data in rows.items():
            try:
                import json as _json
                from datetime import UTC, datetime as _dt
                data = _json.loads(row_data["report_json"])
                # Attempt full model restore; fall back to dict-wrapper
                try:
                    from src.ai.schemas import IntelligenceReport as _IR
                    report = _IR.model_validate(data)
                except Exception:
                    report = _DictReport(data)
                captured_at = row_data.get("captured_at") or _dt.now(UTC)
                self._warm[user_id] = (report, captured_at)
                loaded += 1
            except Exception:
                pass
        from src.platform.logging import get_logger as _gl
        _gl(__name__).info("intelligence_snapshot_store.warm_loaded", count=loaded)
        return loaded


class _DictReport:
    """Minimal dict-backed IntelligenceReport stub for warm-load restore."""
    def __init__(self, data: dict) -> None:
        self._data = data
        for k, v in data.items():
            setattr(self, k, v)
    def model_dump(self) -> dict:
        return self._data


_snapshot_store: IntelligenceSnapshotStore | None = None


def get_intelligence_snapshot() -> IntelligenceSnapshotStore:
    """Return the process-level IntelligenceSnapshotStore singleton.

    Creates on first call. Safe within a single asyncio event loop.
    Inject a custom DashboardTTLCache in tests::

        store = IntelligenceSnapshotStore(cache=DashboardTTLCache())
    """
    global _snapshot_store
    if _snapshot_store is None:
        _snapshot_store = IntelligenceSnapshotStore()
    return _snapshot_store


# ---------------------------------------------------------------------------
# IntelligenceSnapshotSubscriber — event-bus wiring (Gap 2 fix)
# ---------------------------------------------------------------------------

"""IntelligenceSnapshotSubscriber — wire IntelligenceEngineCompletedEvent → store.upsert().

Gap fixed:
    Previously, IntelligenceSnapshotStore.upsert() existed but no subscriber
    called it. bot/api had no guarantee the cache held a fresh report after
    each engine cycle — they would read stale or None.

Wired events:
    IntelligenceEngineCompletedEvent
        → reconstruct a lightweight IntelligenceReport from event fields
          and upsert into the snapshot store.
          Full agent_slots + priority_actions are preserved from the event.

    WatchlistScanCompletedEvent
        → invalidate hot layer only (warm stays for stale-while-revalidate).
          Ensures bot does not serve a stale report from the previous cycle
          after a new watchlist scan fires before the next engine cycle.

Boot (add alongside CacheSubscriber in platform startup)::

    from src.readmodel import IntelligenceSnapshotSubscriber
    IntelligenceSnapshotSubscriber.register()

Idempotent — safe to call multiple times (e.g. during test setup).
"""

from src.platform.event_bus import get_event_bus  # noqa: E402  (after class definition)
from src.platform.events import (  # noqa: E402
    IntelligenceEngineCompletedEvent,
    WatchlistScanCompletedEvent,
)
from src.platform.logging import get_logger  # noqa: E402

_sub_logger = get_logger(__name__ + ".subscriber")
_subscriber_registered = False


class IntelligenceSnapshotSubscriber:
    """Registers event handlers to keep IntelligenceSnapshotStore in sync.

    This is a thin readmodel adapter — no domain logic.
    All it does is call store.upsert() or store.invalidate() in response
    to events emitted by the core segment.
    """

    @staticmethod
    def register(
        store: IntelligenceSnapshotStore | None = None,
    ) -> None:
        """Wire snapshot-upsert handlers onto the global EventBus.

        Args:
            store: Optional custom store instance. Defaults to the
                   process-level singleton (get_intelligence_snapshot()).

        Idempotent — subsequent calls after the first are no-ops.
        """
        global _subscriber_registered
        if _subscriber_registered:
            _sub_logger.debug(
                "IntelligenceSnapshotSubscriber.register() skipped — already registered"
            )
            return

        _store = store or get_intelligence_snapshot()
        bus = get_event_bus()

        @bus.subscribe(IntelligenceEngineCompletedEvent)
        async def _on_engine_completed(
            event: IntelligenceEngineCompletedEvent,
        ) -> None:
            """Upsert IntelligenceReport into snapshot store after each engine cycle.

            Reconstructs a minimal IntelligenceReport from event fields so
            bot/api can read from the store without re-triggering the engine.

            If the event carries full intelligence_report data (Wave C+),
            all agent_slots and priority_actions are preserved.
            """
            if not event.user_id:
                _sub_logger.warning(
                    "snapshot_subscriber.skip_no_user_id",
                    trigger_source=event.trigger_source,
                )
                return

            # Lazy import to avoid circular dependency at module load time.
            # src.ai.schemas is owned by the ai segment; readmodel only reads it.
            from src.ai.schemas.intelligence_report import (  # noqa: PLC0415
                AgentSlot,
                IntelligenceReport,
                PriorityAction,
                RiskFlag,
            )

            # Build AgentSlot list from event — preserve full audit trail.
            agent_slots: list[AgentSlot] = []
            for raw in event.agent_slots or ():
                try:
                    agent_slots.append(
                        AgentSlot.model_validate(raw)
                        if isinstance(raw, dict)
                        else raw
                    )
                except Exception as exc:  # noqa: BLE001
                    _sub_logger.warning(
                        "snapshot_subscriber.agent_slot_parse_error",
                        error=str(exc),
                    )

            # Build PriorityAction list from event.
            priority_actions: list[PriorityAction] = []
            for raw in event.priority_actions or ():
                try:
                    priority_actions.append(
                        PriorityAction.model_validate(raw)
                        if isinstance(raw, dict)
                        else raw
                    )
                except Exception as exc:  # noqa: BLE001
                    _sub_logger.warning(
                        "snapshot_subscriber.priority_action_parse_error",
                        error=str(exc),
                    )

            # Map verdict string to IntelligenceReport.top_verdict Literal.
            # Event.verdict may come from heuristic path (free string) or
            # from IntelligenceReport (already typed). Normalise defensively.
            _VALID_VERDICTS = {
                "BUY_SIGNAL", "SELL_SIGNAL", "HOLD",
                "REVIEW_THESIS", "RISK_ALERT", "NO_ACTION",
            }
            top_verdict = (
                event.verdict
                if event.verdict in _VALID_VERDICTS
                else "NO_ACTION"
            )

            # Build RiskFlag list from event.risk_signals (string tuple).
            # risk_signals are plain string descriptions — wrap as LOW flags
            # so the report contract is satisfied without losing data.
            risk_flags: list[RiskFlag] = []
            for signal_desc in event.risk_signals or ():
                try:
                    risk_flags.append(
                        RiskFlag(
                            flag_type="VOLUME_ANOMALY",  # generic fallback type
                            severity="LOW",
                            description=str(signal_desc)[:200],
                            confirmed_by=[],
                            is_new=True,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    _sub_logger.warning(
                        "snapshot_subscriber.risk_flag_build_error",
                        error=str(exc),
                    )

            report = IntelligenceReport(
                user_id=event.user_id,
                trigger_source=(
                    event.trigger_source
                    if event.trigger_source in (
                        "scheduler_morning", "scheduler_eod", "watchlist_alert",
                        "user_query", "thesis_invalidated", "portfolio_breach", "manual",
                    )
                    else "manual"
                ),
                top_verdict=top_verdict,  # type: ignore[arg-type]
                top_verdict_conviction="medium",
                overall_confidence=float(event.confidence or 0.5),
                narrative_summary=str(event.summary or "")[:800],
                priority_actions=priority_actions,
                risk_flags=risk_flags,
                next_watch_tickers=list(event.next_watch_items or ()),
                agent_slots=agent_slots,
            )

            await _store.upsert(event.user_id, report)

            _sub_logger.info(
                "snapshot_subscriber.upserted",
                user_id=event.user_id,
                top_verdict=top_verdict,
                confidence=report.overall_confidence,
                agent_slot_count=len(agent_slots),
                priority_action_count=len(priority_actions),
                risk_flag_count=len(risk_flags),
                trigger_source=event.trigger_source,
            )

        @bus.subscribe(WatchlistScanCompletedEvent)
        async def _on_scan_completed(
            event: WatchlistScanCompletedEvent,
        ) -> None:
            """Invalidate hot cache after a watchlist scan.

            Warm layer is preserved so bot can still serve stale data
            while the engine cycle for this user is pending.
            """
            if event.user_id:
                _store.invalidate(event.user_id)
                _sub_logger.debug(
                    "snapshot_subscriber.hot_invalidated",
                    user_id=event.user_id,
                    trigger="WatchlistScanCompletedEvent",
                )

        _subscriber_registered = True
        _sub_logger.info(
            "IntelligenceSnapshotSubscriber.registered",
            events=[
                "IntelligenceEngineCompletedEvent",
                "WatchlistScanCompletedEvent",
            ],
        )
