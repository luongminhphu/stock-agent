"""IntelligenceEngine — orchestration core.

Owner: core segment.

Wave 1: signals-based synthesis (rule-based, no AI call).
Wave 2: replace _synthesize() with AIClient.generate_verdict(signals).
Wave 3: dispatch is event-based via _EngineRunner.run_cycle() publishing
        IntelligenceEngineCompletedEvent, consumed by IntelligenceEngineListener
        for Discord delivery. _dispatch() is kept minimal and should not be
        extended with direct bot/briefing calls — new integrations must listen
        on the completed event instead.
Wave B: build IntelligenceReport as the central Investor OS contract while
        keeping EngineVerdict backward-compatible for existing callers.
Wave C: multi-agent orchestrator — _dispatch_to_agents() fans out to real
        AI agents in parallel; _synthesize_agent_outputs() merges results
        into IntelligenceReport via verdict voting + action dedup + risk
        flag merge. Heuristic engine is kept as fallback when no AI client
        is provided (zero breaking changes).

Design principles:
- run_cycle() is the single entry point — snapshot → signals → synthesize → dispatch.
- Each step is replaceable without touching the others.
- All errors are caught per-step; partial output is always returned.
- Agents are optional: each slot degrades gracefully on timeout/error.

Module-level API (used by IntelligenceEngineScheduler):
- get_intelligence_engine(): returns a _EngineRunner singleton.
- run_cycle(user_id, phase, ...): opens its own session, runs full cycle,
  publishes IntelligenceEngineCompletedEvent, returns EngineVerdict | None.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.schemas import (
    AgentSlot,
    IntelligenceReport,
    PriorityAction,
    RiskFlag,
)
from src.core.schemas import (
    EngineOutput,
    EngineVerdict,
    RankedSignal,
    SystemSnapshot,
    VerdictType,
)
from src.core.signals import rank_signals
from src.core.snapshot import SystemSnapshotBuilder
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Per-agent timeout — prevents one slow agent blocking the whole cycle.
_AGENT_TIMEOUT_SECONDS = 25.0

# Verdict priority for voting — higher wins ties.
_VERDICT_PRIORITY: dict[str, int] = {
    "RISK_ALERT":     5,
    "SELL_SIGNAL":    4,
    "REVIEW_THESIS":  3,
    "BUY_SIGNAL":     2,
    "HOLD":           1,
    "NO_ACTION":      0,
}


# ---------------------------------------------------------------------------
# Agent dispatch helpers
# ---------------------------------------------------------------------------

async def _run_with_timeout(
    coro: Any,
    agent_name: str,
    timeout: float = _AGENT_TIMEOUT_SECONDS,
) -> tuple[str, Any, str | None]:
    """Run a coroutine with timeout; return (agent_name, result_or_None, error_summary).

    Never raises — all exceptions are caught and returned as error_summary.
    """
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        return agent_name, result, None
    except asyncio.TimeoutError:
        logger.warning("orchestrator.agent_timeout", agent=agent_name, timeout=timeout)
        return agent_name, None, f"timeout after {timeout}s"
    except Exception as exc:
        logger.warning("orchestrator.agent_error", agent=agent_name, error=str(exc))
        return agent_name, None, str(exc)[:120]


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class IntelligenceEngine:
    """Central AI orchestrator for one investor.

    Usage (heuristic-only, no AI client)::

        engine = IntelligenceEngine(session, user_id)
        output = await engine.run_cycle()

    Usage (full multi-agent)::

        from src.ai.client import AIClient
        engine = IntelligenceEngine(session, user_id, ai_client=AIClient())
        output = await engine.run_cycle()
    """

    DISPATCH_THRESHOLD = 0.65  # only dispatch if confidence >= this

    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        ai_client: Any | None = None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self._ai_client = ai_client  # None → heuristic fallback only

    async def run_cycle(
        self,
        trigger_source: str = "",
        signal_engine_summary: str | None = None,
    ) -> EngineOutput:
        """Full cycle: build snapshot → rank signals → synthesize → dispatch.

        When ai_client is provided, fans out to real agents in parallel and
        synthesizes their outputs into IntelligenceReport (Wave C).
        When ai_client is None, falls back to heuristic engine (Wave B).

        Args:
            trigger_source: caller identity forwarded into the snapshot.
            signal_engine_summary: free-text summary attached to the snapshot.
        """
        snapshot = await SystemSnapshotBuilder(
            self.session,
            self.user_id,
            trigger_source=trigger_source,
            signal_engine_summary=signal_engine_summary,
        ).build()
        signals = rank_signals(snapshot)
        verdict = await self._synthesize(snapshot, signals)

        if self._ai_client is not None:
            intelligence_report = await self._orchestrate(
                snapshot=snapshot,
                signals=signals,
                heuristic_verdict=verdict,
                trigger_source=trigger_source,
            )
        else:
            intelligence_report = self._build_intelligence_report(
                snap=snapshot,
                signals=signals,
                verdict=verdict,
                trigger_source=trigger_source,
            )

        dispatched = await self._dispatch(verdict)
        return EngineOutput(
            snapshot=snapshot,
            verdict=verdict,
            dispatched_to=dispatched,
            intelligence_report=intelligence_report,
        )

    # ------------------------------------------------------------------
    # Wave C: multi-agent orchestration
    # ------------------------------------------------------------------

    async def _orchestrate(
        self,
        snapshot: SystemSnapshot,
        signals: list[RankedSignal],
        heuristic_verdict: EngineVerdict,
        trigger_source: str,
    ) -> IntelligenceReport:
        """Fan-out to real AI agents in parallel, synthesize into IntelligenceReport.

        Agent execution order (all parallel):
          1. ThesisJudgeAgent      — conviction delta per thesis
          2. InvalidationDetector  — breach detection per thesis
          3. NextActionSuggester   — cross-agent action synthesis
          4. PortfolioRiskNarrator — concentration / drawdown narrative

        Each agent runs inside _run_with_timeout() — a failed or timed-out
        agent records status='failed'/'skipped' in its AgentSlot and does
        NOT block the other agents or the final report.
        """
        agent_slots: list[AgentSlot] = []
        agent_results: dict[str, Any] = {}

        # Build per-agent tasks
        tasks = await self._build_agent_tasks(snapshot, signals)

        if not tasks:
            logger.warning("orchestrator.no_tasks_built", user_id=self.user_id)
            return self._build_intelligence_report(
                snap=snapshot,
                signals=signals,
                verdict=heuristic_verdict,
                trigger_source=trigger_source,
            )

        # Run all agents in parallel
        results = await asyncio.gather(
            *[_run_with_timeout(coro, name) for name, coro in tasks],
            return_exceptions=False,
        )

        ran_at = datetime.now(timezone.utc)
        for agent_name, result, error in results:
            if error:
                slot = AgentSlot(
                    agent_name=agent_name,
                    status="failed",
                    output=None,
                    ran_at=ran_at,
                    error_summary=error,
                )
            elif result is None:
                slot = AgentSlot(
                    agent_name=agent_name,
                    status="skipped",
                    output=None,
                    ran_at=ran_at,
                )
            else:
                slot = AgentSlot(
                    agent_name=agent_name,
                    status="ran",
                    output=result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result),
                    ran_at=ran_at,
                )
                agent_results[agent_name] = result
            agent_slots.append(slot)

        # Add heuristic_engine slot for audit trail
        agent_slots.insert(0, AgentSlot(
            agent_name="heuristic_engine",
            status="ran",
            output=heuristic_verdict.model_dump(mode="json"),
            ran_at=ran_at,
        ))

        return self._synthesize_agent_outputs(
            snapshot=snapshot,
            signals=signals,
            heuristic_verdict=heuristic_verdict,
            agent_results=agent_results,
            agent_slots=agent_slots,
            trigger_source=trigger_source,
        )

    async def _build_agent_tasks(
        self,
        snapshot: SystemSnapshot,
        signals: list[RankedSignal],
    ) -> list[tuple[str, Any]]:
        """Build list of (agent_name, coroutine) to fan out.

        Each agent is only included when the snapshot has relevant data.
        This prevents unnecessary AI calls on empty contexts.
        """
        from src.ai.agents.thesis_judge import ThesisJudgeAgent
        from src.ai.agents.invalidation_detector import ThesisInvalidationDetector
        from src.ai.agents.next_action_suggester import NextActionSuggester
        from src.ai.agents.portfolio_risk_narrator import PortfolioRiskNarrator

        tasks: list[tuple[str, Any]] = []

        # 1. ThesisJudge — run when there are thesis due for review
        if snapshot.thesis_due_review:
            thesis_agent = ThesisJudgeAgent(self._ai_client)
            # ThesisJudgeAgent.run() accepts snapshot directly
            tasks.append(("thesis_judge", thesis_agent.run(
                snapshot=snapshot,
                session=self.session,
                user_id=self.user_id,
            )))
        else:
            logger.debug("orchestrator.skip_thesis_judge", reason="no_thesis_due")

        # 2. InvalidationDetector — run when thesis exist
        if snapshot.thesis_due_review or any(
            s.source == "thesis" for s in signals
        ):
            invalidation_agent = ThesisInvalidationDetector(self._ai_client)
            tasks.append(("invalidation_detector", invalidation_agent.run(
                snapshot=snapshot,
                session=self.session,
                user_id=self.user_id,
            )))
        else:
            logger.debug("orchestrator.skip_invalidation", reason="no_thesis_signals")

        # 3. NextActionSuggester — always run if we have any signals
        if signals:
            suggester = NextActionSuggester(self._ai_client)
            contexts = self._build_next_action_contexts(snapshot, signals)
            tasks.append(("next_action_suggester", suggester.suggest(
                contexts=contexts,
                session=self.session,
                user_id=self.user_id,
            )))

        # 4. PortfolioRiskNarrator — run when portfolio has positions
        if snapshot.portfolio.top_exposed_tickers:
            narrator = PortfolioRiskNarrator(self._ai_client)
            tasks.append(("portfolio_risk_narrator", narrator.run(
                snapshot=snapshot,
                session=self.session,
                user_id=self.user_id,
            )))
        else:
            logger.debug("orchestrator.skip_portfolio_risk", reason="no_positions")

        return tasks

    def _build_next_action_contexts(
        self,
        snapshot: SystemSnapshot,
        signals: list[RankedSignal],
    ) -> list[dict[str, Any]]:
        """Build NextActionSuggester context dicts from snapshot + signals.

        Maps snapshot.thesis_due_review → ticker/thesis context.
        Maps signals → urgency/top_signals per ticker.
        """
        # Index signals by ticker (best-effort from description)
        contexts: list[dict[str, Any]] = []

        # Use thesis_due_review as primary context source
        for thesis_ref in snapshot.thesis_due_review[:8]:
            ticker = thesis_ref.ticker
            # Find matching signals for this ticker
            ticker_signals = [
                s for s in signals
                if ticker.upper() in s.description.upper()
            ]
            top_urgency = max(
                (s.urgency_score for s in ticker_signals), default=0.0
            )
            ctx: dict[str, Any] = {
                "ticker": ticker,
                "thesis_id": str(getattr(thesis_ref, "thesis_id", "") or ""),
                "thesis_title": getattr(thesis_ref, "thesis_title", "") or "",
                "signal_urgency": "HIGH" if top_urgency >= 0.65 else "MEDIUM" if top_urgency >= 0.40 else "LOW",
                "top_signals": [s.source for s in ticker_signals[:3]],
                "stop_loss_breached": any(
                    "breach" in s.description.lower() and ticker.upper() in s.description.upper()
                    for s in signals
                ),
            }
            contexts.append(ctx)

        # If no thesis context, fall back to top signal tickers
        if not contexts:
            for ticker in snapshot.watchlist.top_tickers[:5]:
                contexts.append({
                    "ticker": ticker,
                    "signal_urgency": "MEDIUM",
                    "top_signals": ["watchlist"],
                })

        return contexts

    def _synthesize_agent_outputs(
        self,
        snapshot: SystemSnapshot,
        signals: list[RankedSignal],
        heuristic_verdict: EngineVerdict,
        agent_results: dict[str, Any],
        agent_slots: list[AgentSlot],
        trigger_source: str,
    ) -> IntelligenceReport:
        """Merge multi-agent results into a single IntelligenceReport.

        Synthesis strategy:
          - top_verdict:    voted from heuristic + ThesisJudge + InvalidationDetector
                            using _VERDICT_PRIORITY; highest wins.
          - priority_actions: from NextActionPlan.actions[:5], deduped by ticker.
                            Falls back to heuristic when suggester failed.
          - risk_flags:     merged from InvalidationDetector + PortfolioRiskNarrator
                            + heuristic signals. Deduped by (flag_type, ticker).
          - next_watch_tickers: union of all agents' outputs.
          - overall_confidence: weighted average — AI agents count 0.7 weight,
                            heuristic 0.3 when agents ran successfully.
        """
        # --- Verdict voting ---
        top_verdict = self._vote_verdict(
            heuristic_verdict=heuristic_verdict,
            agent_results=agent_results,
        )

        # --- Priority actions from NextActionSuggester ---
        priority_actions = self._extract_priority_actions(agent_results)
        if not priority_actions:
            priority_actions = self._build_priority_actions(heuristic_verdict)

        # --- Risk flags: merge heuristic + agent outputs ---
        risk_flags = self._merge_risk_flags(
            signals=signals,
            agent_results=agent_results,
        )

        # --- Next watch tickers: union ---
        next_watch_tickers = self._merge_next_watch_tickers(
            snapshot=snapshot,
            signals=signals,
            agent_results=agent_results,
        )

        # --- Confidence: weighted average ---
        overall_confidence = self._compute_confidence(
            heuristic_verdict=heuristic_verdict,
            agent_results=agent_results,
        )

        # --- Narrative: from NextActionPlan summary if available ---
        narrative = ""
        if "next_action_suggester" in agent_results:
            plan = agent_results["next_action_suggester"]
            narrative = getattr(plan, "summary", "") or ""
        if not narrative:
            narrative = heuristic_verdict.reasoning_summary

        return IntelligenceReport(
            user_id=self.user_id,
            trigger_source=self._normalize_trigger_source(trigger_source),
            top_verdict=top_verdict,
            top_verdict_conviction=self._confidence_to_conviction(overall_confidence),
            overall_confidence=overall_confidence,
            priority_actions=priority_actions[:5],
            risk_flags=risk_flags[:10],
            next_watch_tickers=next_watch_tickers[:10],
            narrative_summary=narrative[:800],
            agent_slots=agent_slots,
            ttl_minutes=self._default_ttl_minutes(trigger_source),
        )

    def _vote_verdict(
        self,
        heuristic_verdict: EngineVerdict,
        agent_results: dict[str, Any],
    ) -> str:
        """Collect verdict signals from all agents; highest priority wins."""
        candidates: list[str] = [heuristic_verdict.verdict]

        # ThesisJudge verdict → map to engine verdict
        judge_output = agent_results.get("thesis_judge")
        if judge_output is not None:
            raw = getattr(judge_output, "verdict", None)
            mapped = self._map_judge_verdict(str(raw) if raw else "")
            if mapped:
                candidates.append(mapped)

        # InvalidationDetector verdict
        inv_output = agent_results.get("invalidation_detector")
        if inv_output is not None:
            raw = getattr(inv_output, "verdict", None)
            if str(raw) in ("CONFIRMED", "CONFIRMED_INVALID"):
                candidates.append("RISK_ALERT")
            elif str(raw) in ("SUSPECTED", "WEAKENING"):
                candidates.append("REVIEW_THESIS")

        # Pick highest priority
        return max(candidates, key=lambda v: _VERDICT_PRIORITY.get(v, 0))

    def _map_judge_verdict(self, raw: str) -> str | None:
        mapping = {
            "WEAKENING":   "REVIEW_THESIS",
            "INVALIDATED": "RISK_ALERT",
            "CONFIRMED_INVALID": "RISK_ALERT",
            "ON_TRACK":    "HOLD",
            "IMPROVING":   "BUY_SIGNAL",
        }
        return mapping.get(raw)

    def _extract_priority_actions(
        self, agent_results: dict[str, Any]
    ) -> list[PriorityAction]:
        """Convert NextActionPlan.actions → PriorityAction list."""
        plan = agent_results.get("next_action_suggester")
        if plan is None:
            return []

        actions = getattr(plan, "actions", []) or []
        priority_actions: list[PriorityAction] = []
        seen_tickers: set[str] = set()

        urgency_map = {
            "critical": "immediate",
            "high": "today",
            "medium": "this_week",
            "low": "this_week",
        }
        action_type_map = {
            "THESIS_INVALIDATE": "CONSIDER_EXIT",
            "THESIS_REVIEW":     "REVIEW_THESIS",
            "SIGNAL_RESPOND":    "CHECK_STOP_LOSS",
            "WATCHLIST_MONITOR": "MONITOR",
        }

        for rank, action in enumerate(actions[:5], start=1):
            ticker = getattr(action, "ticker", None)
            # Dedup per ticker — keep highest urgency (already sorted)
            if ticker and ticker != "PORTFOLIO" and ticker in seen_tickers:
                continue
            if ticker:
                seen_tickers.add(ticker)

            scope_str = str(getattr(action, "scope", "") or "")
            action_type = action_type_map.get(scope_str, "MONITOR")

            urgency_raw = str(getattr(action, "urgency", "low") or "low").lower()
            urgency = urgency_map.get(urgency_raw, "this_week")

            priority_actions.append(
                PriorityAction(
                    rank=rank,
                    ticker=ticker,
                    action_type=action_type,  # type: ignore[arg-type]
                    urgency=urgency,  # type: ignore[arg-type]
                    instruction=str(getattr(action, "step", "") or "")[:200],
                    source_agent="next_action_suggester",
                    reasoning=str(getattr(action, "rationale", "") or "")[:150],
                )
            )

        return priority_actions

    def _merge_risk_flags(
        self,
        signals: list[RankedSignal],
        agent_results: dict[str, Any],
    ) -> list[RiskFlag]:
        """Merge heuristic risk flags + agent-sourced flags. Dedup by (type, ticker)."""
        flags: list[RiskFlag] = []
        seen: set[tuple[str, str | None]] = set()

        def _add(flag: RiskFlag) -> None:
            key = (flag.flag_type, flag.ticker)
            if key not in seen:
                seen.add(key)
                flags.append(flag)

        # Heuristic flags from signals
        for f in self._build_risk_flags(signals):
            _add(f)

        # InvalidationDetector signals
        inv_output = agent_results.get("invalidation_detector")
        if inv_output is not None:
            inv_signals = getattr(inv_output, "signals", []) or []
            for sig in inv_signals[:5]:
                breach = str(getattr(sig, "breach_type", "") or "")
                ticker = getattr(sig, "ticker", None)
                if breach:
                    flag_type = (
                        "THESIS_INVALIDATED" if breach in ("FUNDAMENTAL", "PRICE")
                        else "STOP_LOSS_BREACH" if breach == "STOP_LOSS"
                        else "VOLUME_ANOMALY"
                    )
                    _add(RiskFlag(
                        flag_type=flag_type,  # type: ignore[arg-type]
                        ticker=ticker,
                        severity="HIGH",
                        description=str(getattr(sig, "description", "") or "")[:200],
                        confirmed_by=["invalidation_detector"],
                        is_new=True,
                    ))

        # PortfolioRiskNarrator chapters
        portfolio_output = agent_results.get("portfolio_risk_narrator")
        if portfolio_output is not None:
            chapters = getattr(portfolio_output, "chapters", []) or []
            for chapter in chapters[:3]:
                theme = str(getattr(chapter, "risk_theme", "") or "")
                if "concentration" in theme.lower():
                    flag_type = "CONCENTRATION_RISK"
                elif "sector" in theme.lower():
                    flag_type = "SECTOR_ROTATION_ADVERSE"
                else:
                    flag_type = "MARKET_TREND_REVERSAL"
                severity_raw = str(getattr(chapter, "severity", "MEDIUM") or "MEDIUM").upper()
                severity = severity_raw if severity_raw in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else "MEDIUM"
                _add(RiskFlag(
                    flag_type=flag_type,  # type: ignore[arg-type]
                    ticker=None,
                    severity=severity,  # type: ignore[arg-type]
                    description=str(getattr(chapter, "summary", "") or "")[:200],
                    confirmed_by=["portfolio_risk_narrator"],
                    is_new=True,
                ))

        return flags

    def _merge_next_watch_tickers(
        self,
        snapshot: SystemSnapshot,
        signals: list[RankedSignal],
        agent_results: dict[str, Any],
    ) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []

        def _add(ticker: str) -> None:
            t = ticker.upper().strip()
            if t and t not in seen:
                seen.add(t)
                ordered.append(t)

        # Highest urgency: tickers from NextActionSuggester critical/high actions
        plan = agent_results.get("next_action_suggester")
        if plan is not None:
            for action in (getattr(plan, "actions", []) or []):
                urgency = str(getattr(action, "urgency", "") or "")
                ticker = getattr(action, "ticker", None)
                if ticker and urgency in ("critical", "high"):
                    _add(ticker)

        # Then heuristic next_watch_items
        for item in self._extract_next_watch_tickers(snapshot, signals):
            _add(item)

        return ordered[:10]

    def _compute_confidence(
        self,
        heuristic_verdict: EngineVerdict,
        agent_results: dict[str, Any],
    ) -> float:
        """Weighted average confidence.

        When AI agents ran: their outputs carry 0.7 weight, heuristic 0.3.
        When no agents ran: use heuristic confidence directly.
        """
        ai_confidences: list[float] = []

        judge = agent_results.get("thesis_judge")
        if judge is not None:
            c = getattr(judge, "confidence", None)
            if c is not None:
                ai_confidences.append(float(c))

        plan = agent_results.get("next_action_suggester")
        if plan is not None:
            actions = getattr(plan, "actions", []) or []
            if actions:
                avg = sum(float(getattr(a, "confidence", 0.5)) for a in actions) / len(actions)
                ai_confidences.append(avg)

        if not ai_confidences:
            return heuristic_verdict.confidence

        ai_avg = sum(ai_confidences) / len(ai_confidences)
        return round(0.7 * ai_avg + 0.3 * heuristic_verdict.confidence, 3)

    # ------------------------------------------------------------------
    # Wave B: heuristic-only synthesis (fallback when no AI client)
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        snap: SystemSnapshot,
        signals: list[RankedSignal],
    ) -> EngineVerdict:
        """Derive verdict from ranked signals using priority rules."""
        if not signals:
            return self._no_action_verdict(snap)

        top = signals[0]
        verdict_type, confidence = self._map_signal_to_verdict(top)

        risk_signals = [
            s.description for s in signals if s.source in ("portfolio", "watchlist")
        ][:5]
        next_watch = [
            s.description for s in signals if s.source in ("thesis", "market")
        ][:5]
        sources = list({s.source for s in signals})

        summary = " | ".join(
            f"{s.source}:{s.urgency_score:.2f}" for s in signals[:4]
        )

        return EngineVerdict(
            verdict_id=str(uuid.uuid4()),
            verdict=verdict_type,
            confidence=confidence,
            risk_signals=risk_signals,
            next_watch_items=next_watch,
            action=self._derive_action(verdict_type, snap),
            reasoning_summary=summary,
            sources=sources,
            generated_at=datetime.now(timezone.utc),
        )

    def _build_intelligence_report(
        self,
        snap: SystemSnapshot,
        signals: list[RankedSignal],
        verdict: EngineVerdict,
        trigger_source: str,
    ) -> IntelligenceReport:
        """Build IntelligenceReport from heuristic-only output (Wave B fallback)."""
        slot = AgentSlot(
            agent_name="heuristic_engine",
            status="ran",
            ran_at=verdict.generated_at,
            output=verdict.model_dump(mode="json"),
        )

        priority_actions = self._build_priority_actions(verdict)
        risk_flags = self._build_risk_flags(signals)
        next_watch_tickers = self._extract_next_watch_tickers(snap, signals)

        return IntelligenceReport(
            user_id=self.user_id,
            trigger_source=self._normalize_trigger_source(trigger_source),
            top_verdict=verdict.verdict,
            top_verdict_conviction=self._confidence_to_conviction(verdict.confidence),
            overall_confidence=verdict.confidence,
            priority_actions=priority_actions,
            risk_flags=risk_flags,
            next_watch_tickers=next_watch_tickers,
            narrative_summary=verdict.reasoning_summary,
            agent_slots=[slot],
            ttl_minutes=self._default_ttl_minutes(trigger_source),
        )

    def _build_priority_actions(self, verdict: EngineVerdict) -> list[PriorityAction]:
        if verdict.verdict == "NO_ACTION":
            return [
                PriorityAction(
                    rank=1,
                    ticker=None,
                    action_type="NO_ACTION",
                    urgency="this_week",
                    instruction=verdict.action,
                    source_agent="heuristic_engine",
                    reasoning=verdict.reasoning_summary[:150],
                )
            ]

        action_type_map = {
            "RISK_ALERT":    "CHECK_STOP_LOSS",
            "REVIEW_THESIS": "REVIEW_THESIS",
            "BUY_SIGNAL":    "CONSIDER_ENTRY",
            "SELL_SIGNAL":   "CONSIDER_EXIT",
            "HOLD":          "MONITOR",
        }
        urgency_map = {
            "RISK_ALERT":    "immediate",
            "REVIEW_THESIS": "today",
            "BUY_SIGNAL":    "today",
            "SELL_SIGNAL":   "immediate",
            "HOLD":          "this_week",
        }
        return [
            PriorityAction(
                rank=1,
                ticker=None,
                action_type=action_type_map.get(verdict.verdict, "MONITOR"),
                urgency=urgency_map.get(verdict.verdict, "this_week"),
                instruction=verdict.action,
                source_agent="heuristic_engine",
                reasoning=verdict.reasoning_summary[:150],
            )
        ]

    def _build_risk_flags(self, signals: list[RankedSignal]) -> list[RiskFlag]:
        flags: list[RiskFlag] = []
        for signal in signals[:5]:
            severity = self._urgency_to_severity(signal.urgency_score)
            flag_type = self._signal_to_flag_type(signal)
            if flag_type is None:
                continue
            flags.append(
                RiskFlag(
                    flag_type=flag_type,
                    ticker=None,
                    severity=severity,
                    description=signal.description[:200],
                    confirmed_by=[f"signal:{signal.source}"],
                    is_new=True,
                )
            )
        return flags

    def _extract_next_watch_tickers(
        self,
        snap: SystemSnapshot,
        signals: list[RankedSignal],
    ) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []

        def _add(ticker: str) -> None:
            t = ticker.upper().strip()
            if t and t not in seen:
                seen.add(t)
                ordered.append(t)

        for thesis_ref in snap.thesis_due_review[:5]:
            _add(thesis_ref.ticker)
        for signal in snap.market_anomalies[:5]:
            _add(signal.ticker)
        for ticker in snap.watchlist.top_tickers[:5]:
            _add(ticker)
        for ticker in snap.market.top_opportunity_tickers[:5]:
            _add(ticker)

        return ordered[:10]

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _normalize_trigger_source(self, trigger_source: str) -> str:
        mapping = {
            "scheduler":          "scheduler_morning",
            "scheduler_morning":  "scheduler_morning",
            "scheduler_eod":      "scheduler_eod",
            "discord_command":    "user_query",
            "user_query":         "user_query",
            "manual":             "manual",
            "watchlist_alert":    "watchlist_alert",
            "thesis_invalidated": "thesis_invalidated",
            "portfolio_breach":   "portfolio_breach",
        }
        return mapping.get(trigger_source or "manual", "manual")

    def _confidence_to_conviction(self, confidence: float) -> str:
        if confidence >= 0.8:
            return "high"
        if confidence >= 0.55:
            return "medium"
        return "low"

    def _default_ttl_minutes(self, trigger_source: str) -> int:
        normalized = self._normalize_trigger_source(trigger_source)
        if normalized in ("thesis_invalidated", "portfolio_breach"):
            return 15
        if normalized == "scheduler_morning":
            return 240
        return 60

    def _urgency_to_severity(self, urgency_score: float) -> str:
        if urgency_score >= 0.85:
            return "CRITICAL"
        if urgency_score >= 0.65:
            return "HIGH"
        if urgency_score >= 0.40:
            return "MEDIUM"
        return "LOW"

    def _signal_to_flag_type(self, signal: RankedSignal) -> str | None:
        description = signal.description.lower()
        if signal.source == "portfolio":
            if "breach" in description:
                return "STOP_LOSS_BREACH"
            return "CONCENTRATION_RISK"
        if signal.source == "thesis":
            if "invalidate" in description:
                return "THESIS_INVALIDATED"
            return "MARKET_TREND_REVERSAL"
        if signal.source == "watchlist":
            return "VOLUME_ANOMALY"
        if signal.source == "market":
            return "MARKET_TREND_REVERSAL"
        return None

    def _map_signal_to_verdict(
        self, top: RankedSignal
    ) -> tuple[VerdictType, float]:
        if top.source == "portfolio":
            return "RISK_ALERT", min(0.95, 0.70 + top.urgency_score * 0.25)
        if top.source == "thesis" and "invalidate" in top.description.lower():
            return "REVIEW_THESIS", min(0.90, 0.65 + top.urgency_score * 0.25)
        if top.source == "watchlist":
            return "RISK_ALERT", min(0.85, 0.60 + top.urgency_score * 0.25)
        if top.source == "thesis":
            return "REVIEW_THESIS", min(0.80, 0.55 + top.urgency_score * 0.25)
        if top.source == "market" and top.urgency_score >= 0.4:
            return "HOLD", min(0.75, 0.50 + top.urgency_score * 0.25)
        return "NO_ACTION", 0.40

    def _no_action_verdict(self, snap: SystemSnapshot) -> EngineVerdict:
        return EngineVerdict(
            verdict_id=str(uuid.uuid4()),
            verdict="NO_ACTION",
            confidence=0.40,
            risk_signals=[],
            next_watch_items=[],
            action="Không có action ưu tiên. Hệ thống ổn định.",
            reasoning_summary="0 signals detected across all segments",
            sources=[],
            generated_at=datetime.now(timezone.utc),
        )

    def _derive_action(self, verdict: VerdictType, snap: SystemSnapshot) -> str:
        if verdict == "RISK_ALERT":
            tickers = ", ".join(
                a.ticker for a in snap.watchlist_alerts[:3]
            ) or ", ".join(snap.portfolio.top_exposed_tickers[:3])
            return f"Kiểm tra ngay: {tickers}" if tickers else "Kiểm tra risk breach"
        if verdict == "REVIEW_THESIS":
            tickers = ", ".join(t.ticker for t in snap.thesis_due_review[:3])
            return f"Review thesis: {tickers}" if tickers else "Review thesis overdue"
        if verdict == "HOLD":
            tickers = ", ".join(s.ticker for s in snap.market_anomalies[:3])
            return f"Theo dõi tín hiệu thị trường: {tickers}" if tickers else "Theo dõi thị trường"
        return "Không có action ưu tiên. Hệ thống ổn định."

    async def _dispatch(self, verdict: EngineVerdict) -> list[str]:
        """Record internal dispatch decision for observability.

        Currently returns ["log"] when confidence passes the threshold.
        Kept for backward compatibility with EngineOutput.dispatched_to.
        External integrations must subscribe to IntelligenceEngineCompletedEvent.
        """
        dispatched: list[str] = []
        if verdict.confidence >= self.DISPATCH_THRESHOLD:
            dispatched.append("log")
        return dispatched


# ---------------------------------------------------------------------------
# Module-level runner (singleton used by scheduler)
# ---------------------------------------------------------------------------


class _EngineRunner:
    """Stateless runner — holds no session, safe as a module singleton.

    Wave C: accepts optional ai_client injection.
    When ai_client is provided, IntelligenceEngine will use multi-agent
    orchestration. Otherwise falls back to heuristic engine.
    """

    def __init__(self, ai_client: Any | None = None) -> None:
        self._ai_client = ai_client

    async def run_cycle(
        self,
        user_id: str,
        phase: str = "morning",
        triggered_by: str = "scheduler",
        signal_engine_summary: str = "",
        verdict_agent: Any | None = None,
        context_hint: str | None = None,
        trigger_source: str = "",
        priority: str = "normal",
        ai_client: Any | None = None,
    ) -> EngineVerdict | None:
        """Run a full intelligence cycle for one user.

        Args:
            ai_client: Optional per-call override. Falls back to runner-level
                       ai_client, then to heuristic-only mode.
        """
        from src.platform.db import AsyncSessionLocal

        effective_trigger = trigger_source or triggered_by
        effective_client = ai_client or self._ai_client

        logger.info(
            "engine.run_cycle.start",
            user_id=user_id,
            phase=phase,
            triggered_by=triggered_by,
            trigger_source=effective_trigger,
            has_signal_summary=bool(signal_engine_summary),
            has_verdict_agent=verdict_agent is not None,
            orchestration_mode="multi_agent" if effective_client else "heuristic",
        )

        try:
            async with AsyncSessionLocal() as session:
                engine = IntelligenceEngine(
                    session=session,
                    user_id=user_id,
                    ai_client=effective_client,
                )
                output = await engine.run_cycle(
                    trigger_source=effective_trigger,
                    signal_engine_summary=signal_engine_summary or None,
                )
            verdict = output.verdict
        except Exception as exc:
            logger.error(
                "engine.run_cycle.snapshot_failed",
                user_id=user_id,
                phase=phase,
                error=str(exc),
            )
            return None

        if verdict_agent is not None:
            try:
                ranked_signals = rank_signals(output.snapshot)
                ai_verdict = await verdict_agent.run(
                    snapshot=output.snapshot,
                    ranked_signals=ranked_signals,
                    session=session,
                    user_id=user_id,
                )
                if ai_verdict is not None:
                    verdict = ai_verdict
                    logger.info(
                        "engine.run_cycle.ai_verdict_applied",
                        verdict=verdict.verdict,
                        confidence=verdict.confidence,
                    )
            except Exception as exc:
                logger.warning(
                    "engine.run_cycle.ai_verdict_failed",
                    error=str(exc),
                    fallback="using_heuristic_verdict",
                )

        flagged_tickers = _extract_snapshot_tickers(output.snapshot)

        try:
            from src.platform.event_bus import get_event_bus
            from src.platform.events import IntelligenceEngineCompletedEvent

            completed = IntelligenceEngineCompletedEvent(
                user_id=user_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                action_required=verdict.verdict not in ("NO_ACTION", "HOLD"),
                summary=verdict.action,
                trigger_source=effective_trigger,
                flagged_tickers=flagged_tickers,
            )
            bus = get_event_bus()
            await bus.publish(completed)

            logger.info(
                "engine.run_cycle.completed_event_published",
                user_id=user_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                phase=phase,
                action_required=completed.action_required,
                flagged_ticker_count=len(flagged_tickers),
                has_intelligence_report=output.intelligence_report is not None,
                orchestration_mode="multi_agent" if effective_client else "heuristic",
            )
        except Exception as exc:
            logger.error(
                "engine.run_cycle.event_publish_failed",
                error=str(exc),
                verdict=verdict.verdict,
            )

        return verdict


def _extract_snapshot_tickers(snapshot: SystemSnapshot) -> tuple[str, ...]:
    """Extract all flagged tickers from a SystemSnapshot."""
    seen: set[str] = set()
    result: list[str] = []

    def _add(ticker: str) -> None:
        t = ticker.upper().strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    for alert in snapshot.watchlist_alerts:
        _add(alert.ticker)
    for thesis_ref in snapshot.thesis_due_review:
        _add(thesis_ref.ticker)
    for ticker in snapshot.portfolio.top_exposed_tickers:
        _add(ticker)
    for ticker in snapshot.watchlist.top_tickers:
        _add(ticker)

    return tuple(result)


_engine_runner: _EngineRunner | None = None


def get_intelligence_engine() -> _EngineRunner:
    """Return the module-level _EngineRunner singleton.

    Wave C: to enable multi-agent mode, inject AIClient:
        from src.ai.client import AIClient
        runner = get_intelligence_engine()
        runner._ai_client = AIClient()  # or pass at construction time
    """
    global _engine_runner
    if _engine_runner is None:
        _engine_runner = _EngineRunner()
        logger.info("engine.runner_singleton_created")
    return _engine_runner
