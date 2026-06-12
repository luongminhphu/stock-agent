"""
IntelligenceEngineListener — EventBus subscriber wiring the core engine.

Owner: core segment.

Consumed events:
  - IntelligenceEngineRequestedEvent   (primary trigger)
  - PortfolioSnapshotReadyEvent        (portfolio context injector)

Emitted event  : IntelligenceEngineCompletedEvent
Side-effect    : None. Discord delivery is handled by
                 src.bot.intelligence_engine_subscriber.IntelligenceEngineSubscriber,
                 which subscribes to IntelligenceEngineCompletedEvent.

Wave 3 change:
    Passes event.signal_engine_summary into engine.run_cycle() so the
    AI verdict prompt receives richer cross-segment context.
    verdict_event_id echoed on IntelligenceEngineCompletedEvent so
    downstream feedback submissions can reference it.

Wave C (intelligence_report wire):
    Maps EngineOutput.intelligence_report into IntelligenceEngineCompletedEvent
    so downstream subscribers (Discord embed builder, GlobalRiskSubscriber,
    future readmodel intelligence_snapshot) receive full multi-agent output
    (agent_slots + priority_actions) instead of heuristic-only verdict fields.
    Backward-compatible — existing consumers that only read
    verdict/confidence/action_required/summary are unaffected.

Portfolio injection (next wave):
    PortfolioSnapshotReadyEvent (emitted at 08:15 ICT by PortfolioSnapshotScheduler)
    is cached per user_id. When IntelligenceEngineRequestedEvent arrives (08:35 ICT),
    top_exposed_tickers and unrealized_pnl_pct are appended to context_hint so the
    AI verdict prompt receives portfolio-aware context.
    Cache is cleared after each engine cycle to avoid stale data.

Boot: call IntelligenceEngineListener(...).register() in platform bootstrap.
"""
from __future__ import annotations

import uuid
from typing import Any

from src.core import engine
from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import (
    IntelligenceEngineCompletedEvent,
    IntelligenceEngineRequestedEvent,
    PortfolioSnapshotReadyEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class IntelligenceEngineListener:
    """Subscribe to IntelligenceEngineRequestedEvent → run engine cycle → emit CompletedEvent.

    Also subscribes to PortfolioSnapshotReadyEvent to cache portfolio context
    (top_exposed_tickers, unrealized_pnl_pct) per user_id. The cached context is
    injected into engine.run_cycle() via context_hint enrichment then cleared.

    This class is owned by the *core* segment and must not import from src.bot.*.
    All Discord/notification side-effects are handled by downstream subscribers
    of IntelligenceEngineCompletedEvent (e.g. IntelligenceEngineSubscriber in bot).

    Args:
        bus:          EventBus instance. Defaults to get_event_bus() singleton.
        verdict_agent: Optional IntelligenceVerdictAgent (ai segment).
                       When provided, Wave 2 AI synthesis is active.
                       When None (default), Wave 1 heuristic runs only.
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        verdict_agent: Any | None = None,
    ) -> None:
        self._bus = bus or get_event_bus()
        self._verdict_agent = verdict_agent
        # Cache keyed by user_id — stores latest PortfolioSnapshotReadyEvent
        self._portfolio_context: dict[str, PortfolioSnapshotReadyEvent] = {}

    def register(self) -> None:
        self._bus.subscribe_handler(IntelligenceEngineRequestedEvent, self._handle)
        self._bus.subscribe_handler(PortfolioSnapshotReadyEvent, self._handle_portfolio_snapshot)
        logger.info(
            "intelligence_listener.registered",
            wave="2_ai" if self._verdict_agent is not None else "1_heuristic",
        )

    # ------------------------------------------------------------------
    # Portfolio snapshot cache — runs ~20 min before engine cycle
    # ------------------------------------------------------------------

    async def _handle_portfolio_snapshot(
        self, event: PortfolioSnapshotReadyEvent
    ) -> None:
        self._portfolio_context[event.user_id] = event
        logger.info(
            "intelligence_listener.portfolio_snapshot_cached",
            user_id=event.user_id,
            total_positions=event.total_positions,
            unrealized_pnl_pct=event.unrealized_pnl_pct,
            top_exposed_tickers=list(event.top_exposed_tickers),
            snapshot_phase=event.snapshot_phase,
        )

    # ------------------------------------------------------------------
    # Main engine handler
    # ------------------------------------------------------------------

    async def _handle(self, event: IntelligenceEngineRequestedEvent) -> None:
        logger.info(
            "intelligence_listener.received",
            trigger_source=event.trigger_source,
            priority=event.priority,
            user_id=event.user_id,
            has_signal_summary=bool(event.signal_engine_summary),
        )

        # ── Enrich context_hint with portfolio snapshot if cached ───────
        portfolio_snap = self._portfolio_context.pop(event.user_id, None)
        enriched_context_hint: str | None = event.context_hint

        if portfolio_snap is not None:
            portfolio_lines = [
                f"Portfolio snapshot ({portfolio_snap.snapshot_phase}):",
                f"  positions={portfolio_snap.total_positions}",
                f"  nav={portfolio_snap.total_nav:.0f}",
                f"  unrealized_pnl_pct={portfolio_snap.unrealized_pnl_pct:+.2f}%",
                f"  top_exposed={','.join(portfolio_snap.top_exposed_tickers)}",
            ]
            portfolio_block = " | ".join(portfolio_lines)
            enriched_context_hint = (
                f"{event.context_hint} | {portfolio_block}".strip(" | ")
                if event.context_hint
                else portfolio_block
            )
            logger.info(
                "intelligence_listener.portfolio_context_injected",
                user_id=event.user_id,
                unrealized_pnl_pct=portfolio_snap.unrealized_pnl_pct,
                top_exposed_tickers=list(portfolio_snap.top_exposed_tickers),
            )
        else:
            logger.debug(
                "intelligence_listener.no_portfolio_context",
                user_id=event.user_id,
                reason="snapshot_not_cached_or_already_consumed",
            )

        verdict = await engine.run_cycle(
            user_id=event.user_id,
            trigger_source=event.trigger_source,
            priority=event.priority,
            context_hint=enriched_context_hint,
            signal_engine_summary=event.signal_engine_summary,
            verdict_agent=self._verdict_agent,
        )

        if verdict is None:
            logger.info(
                "intelligence_listener.no_verdict",
                trigger_source=event.trigger_source,
                reason="below_threshold_or_snapshot_failed",
            )
            return

        # ── Emit IntelligenceEngineCompletedEvent ────────────────────────
        echoed_verdict_event_id: str = (
            getattr(verdict, "verdict_event_id", None) or str(uuid.uuid4())
        )

        def _to_tuple(val: Any) -> tuple[str, ...]:
            if not val:
                return ()
            return tuple(str(v) for v in val)

        def _to_dict_tuple(val: Any) -> tuple[dict[str, Any], ...]:
            """Coerce a list/tuple of objects into a tuple of plain dicts.

            Handles:
            - objects with .model_dump() (Pydantic v2)
            - objects with .dict()       (Pydantic v1)
            - plain dicts
            - anything else → skipped with a warning
            """
            if not val:
                return ()
            result: list[dict[str, Any]] = []
            for item in val:
                if isinstance(item, dict):
                    result.append(item)
                elif hasattr(item, "model_dump"):
                    result.append(item.model_dump(mode="json"))
                elif hasattr(item, "dict"):
                    result.append(item.dict())
                else:
                    logger.warning(
                        "intelligence_listener.skip_unserializable_item",
                        item_type=type(item).__name__,
                    )
            return tuple(result)

        # Extract intelligence_report fields when available (Wave C multi-agent)
        intelligence_report = getattr(verdict, "intelligence_report", None)
        if intelligence_report is None:
            # verdict is EngineVerdict (heuristic path) — check EngineOutput wrapper
            # engine.run_cycle() returns EngineVerdict directly; EngineOutput is
            # internal. Pull from the EngineOutput if the caller attached it.
            pass

        agent_slots: tuple[dict[str, Any], ...] = ()
        priority_actions: tuple[dict[str, Any], ...] = ()

        if intelligence_report is not None:
            raw_slots = getattr(intelligence_report, "agent_slots", None)
            agent_slots = _to_dict_tuple(raw_slots)

            raw_actions = getattr(intelligence_report, "priority_actions", None)
            priority_actions = _to_dict_tuple(raw_actions)

            logger.info(
                "intelligence_listener.report_mapped",
                agent_slot_count=len(agent_slots),
                priority_action_count=len(priority_actions),
            )
        else:
            logger.debug(
                "intelligence_listener.no_intelligence_report",
                reason="heuristic_path_or_report_not_attached",
            )

        # Extract flagged_tickers from verdict for GlobalRiskSubscriber
        flagged_tickers: tuple[str, ...] = ()
        if portfolio_snap is not None:
            flagged_tickers = portfolio_snap.top_exposed_tickers

        completed = IntelligenceEngineCompletedEvent(
            user_id=event.user_id,
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            action_required=verdict.verdict not in ("NO_ACTION", "HOLD"),
            summary=getattr(verdict, "action", "") or "",
            trigger_source=event.trigger_source,
            verdict_event_id=echoed_verdict_event_id,
            reasoning_summary=getattr(verdict, "reasoning_summary", "") or "",
            risk_signals=_to_tuple(getattr(verdict, "risk_signals", None)),
            next_watch_items=_to_tuple(getattr(verdict, "next_watch_items", None)),
            sources=_to_tuple(getattr(verdict, "sources", None)),
            flagged_tickers=flagged_tickers,
            agent_slots=agent_slots,
            priority_actions=priority_actions,
        )
        await self._bus.publish(completed)

        logger.info(
            "intelligence_listener.completed_emitted",
            verdict=completed.verdict,
            confidence=completed.confidence,
            action_required=completed.action_required,
            verdict_event_id=completed.verdict_event_id,
            risk_signal_count=len(completed.risk_signals),
            next_watch_count=len(completed.next_watch_items),
            agent_slot_count=len(completed.agent_slots),
            priority_action_count=len(completed.priority_actions),
            flagged_ticker_count=len(completed.flagged_tickers),
        )
