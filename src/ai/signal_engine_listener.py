"""SignalEngineListener — ai segment event handler for signal engine pipeline.

Owner: ai segment.
Trigger: SignalEngineRequestedEvent (emitted by bot.SignalEngineScheduler).
Emits:   SignalEngineCompletedEvent (consumed by briefing segment).

Responsibility:
  On event: open DB session, load watchlist + active theses + portfolio context,
  run per-ticker WatchdogAgent, call SignalEngineAgent for cross-check,
  emit SignalEngineCompletedEvent, optionally send to Discord.

Boundary rules:
  - Lives in ai segment — owns AI orchestration only.
  - Does NOT modify thesis, watchlist, or portfolio state.
  - Does NOT schedule itself — timing is bot.SignalEngineScheduler's concern.
  - Does NOT format Discord messages — only sends raw signal_summary.
  - register() wires onto global EventBus. Called once from bootstrap().

Fault tolerance:
  Every step is wrapped in try/except and logged.
  Handler never raises — EventBus worker is always safe.
  SignalEngineCompletedEvent is always emitted (fallback output on AI failure).

Step sequence:
  1. Load watchlist tickers          (watchlist segment)
  2. Load active theses (structured) (thesis segment)
  3. Per-ticker WatchdogAgent.assess (ai segment — graceful None on failure)
  4. Load portfolio data             (portfolio segment)
  5. Load feedback summary           (readmodel segment — optional)
  6. SignalEngineAgent.run(...)       (ai segment — has built-in rule-based fallback)
  7. Emit SignalEngineCompletedEvent  (platform)
  8. Optional Discord send
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.platform.events import SignalEngineRequestedEvent

logger = get_logger(__name__)

# Cap watchdog calls per run to avoid excessive AI spend
MAX_WATCHDOG_TICKERS = 10


class SignalEngineListener:
    """Subscribe SignalEngineRequestedEvent and run the full signal engine pipeline.

    Inject discord.Client after bot login::
        listener = get_signal_engine_listener()
        listener.set_client(bot)  # in bot on_ready
    """

    def __init__(
        self,
        signal_engine_agent: object,
        watchdog_agent: object,
        session_factory: object,
        morning_channel_id: int | None = None,
        user_id: str | None = None,
    ) -> None:
        self._engine = signal_engine_agent
        self._watchdog = watchdog_agent
        self._session_factory = session_factory
        self._morning_channel_id = morning_channel_id
        self._user_id = user_id
        self._discord_client: object | None = None

    def set_client(self, client: object) -> None:
        """Inject discord.Client after bot login. Safe to call multiple times."""
        self._discord_client = client
        logger.info("signal_engine_listener.client_set")

    def register(self) -> None:
        """Subscribe _handle to SignalEngineRequestedEvent on global EventBus."""
        from src.platform.event_bus import get_event_bus
        from src.platform.events import SignalEngineRequestedEvent

        bus = get_event_bus()
        bus.subscribe_handler(SignalEngineRequestedEvent, self._handle)
        logger.info("signal_engine_listener.registered")

    async def _handle(self, event: "SignalEngineRequestedEvent") -> None:
        """Handle SignalEngineRequestedEvent — never raises."""
        logger.info(
            "signal_engine_listener.triggered",
            event_id=event.event_id,
            triggered_by=getattr(event, "triggered_by", "scheduler"),
        )

        if not self._user_id:
            logger.warning("signal_engine_listener.no_user_id")
            return

        # Step 1–5: collect all inputs
        tickers = await self._load_watchlist_tickers()
        active_theses = await self._load_active_theses()
        watchdog_outputs = await self._run_watchdog(tickers, active_theses)
        portfolio_data = await self._load_portfolio_data()
        feedback_summary = await self._load_feedback_summary()

        # Step 6: run signal engine
        output = await self._run_signal_engine(
            watchdog_outputs=watchdog_outputs,
            active_theses=active_theses,
            portfolio_data=portfolio_data,
            feedback_summary=feedback_summary,
        )

        if output is None:
            logger.warning("signal_engine_listener.no_output")
            return

        # Step 7: emit completion event
        await self._emit_completed(output, event)

        # Step 8: optional Discord send
        await self._send_to_discord(output)

    # ------------------------------------------------------------------
    # Step helpers
    # ------------------------------------------------------------------

    async def _load_watchlist_tickers(self) -> list[str]:
        try:
            from src.platform.db import AsyncSessionLocal
            from src.watchlist.service import WatchlistService

            async with AsyncSessionLocal() as session:
                svc = WatchlistService(session=session)
                items = await svc.list_items(user_id=self._user_id)
            tickers = [item.ticker for item in items]
            logger.info("signal_engine_listener.watchlist_loaded", count=len(tickers))
            return tickers
        except Exception as exc:
            logger.warning("signal_engine_listener.watchlist_failed", error=str(exc))
            return []

    async def _load_active_theses(self) -> list[dict]:
        """Load active theses as structured dicts for SignalEngineAgent deep cross-check.

        Includes assumptions, catalysts, and invalidation_conditions so the agent
        can apply prompt rule 12 (thesis cross-check).
        """
        try:
            from src.platform.db import AsyncSessionLocal
            from src.thesis.service import ThesisService

            async with AsyncSessionLocal() as session:
                svc = ThesisService(session=session)
                theses = await svc.list_for_user(
                    user_id=self._user_id, status="active"
                )

            result = []
            for t in theses:
                assumptions = [
                    {"description": getattr(a, "description", str(a)),
                     "current_status": getattr(a, "current_status", "pending"),
                     "last_note": getattr(a, "last_note", "")}
                    for a in (getattr(t, "assumptions", []) or [])
                ]
                catalysts = getattr(t, "catalysts", []) or []
                invalidation = getattr(t, "invalidation_conditions", []) or []
                result.append({
                    "id": t.id,
                    "ticker": t.ticker,
                    "title": t.title,
                    "status": "active",
                    "summary": getattr(t, "summary", ""),
                    "stop_loss": getattr(t, "stop_loss", None),
                    "target_price": getattr(t, "target_price", None),
                    "entry_price": getattr(t, "entry_price", None),
                    "assumptions": assumptions,
                    "catalysts": [getattr(c, "description", str(c)) for c in catalysts],
                    "invalidation_conditions": [
                        getattr(i, "description", str(i)) for i in invalidation
                    ],
                })
            logger.info("signal_engine_listener.theses_loaded", count=len(result))
            return result
        except Exception as exc:
            logger.warning("signal_engine_listener.theses_failed", error=str(exc))
            return []

    async def _run_watchdog(
        self,
        tickers: list[str],
        active_theses: list[dict],
    ) -> list[dict]:
        """Run WatchdogAgent per thesis. Returns list of WatchdogOutput dicts.

        Caps at MAX_WATCHDOG_TICKERS to control AI spend.
        Failures per ticker are logged and skipped — never block the pipeline.
        """
        if not active_theses:
            return []

        from src.ai.prompts.watchdog import AssumptionSnapshot, WatchdogContext
        from src.platform.bootstrap import get_quote_service

        # Build ticker → current_price map from quote service
        price_map: dict[str, float] = {}
        try:
            quote_service = get_quote_service()
            thesis_tickers = list({t["ticker"] for t in active_theses})
            quotes = await quote_service.get_bulk_quotes(thesis_tickers)
            price_map = {q.ticker: q.price for q in quotes}
        except Exception as exc:
            logger.warning("signal_engine_listener.watchdog_quotes_failed", error=str(exc))

        outputs: list[dict] = []
        for thesis in active_theses[:MAX_WATCHDOG_TICKERS]:
            try:
                assumptions = [
                    AssumptionSnapshot(
                        assumption_id=idx,
                        description=a.get("description", ""),
                        current_status=a.get("current_status", "pending"),
                        last_note=a.get("last_note", ""),
                    )
                    for idx, a in enumerate(thesis.get("assumptions", []))
                ]
                ctx = WatchdogContext(
                    thesis_id=thesis["id"],
                    ticker=thesis["ticker"],
                    thesis_title=thesis["title"],
                    thesis_summary=thesis.get("summary", ""),
                    assumptions=assumptions,
                    current_price=price_map.get(thesis["ticker"]),
                    entry_price=thesis.get("entry_price"),
                    stop_loss=thesis.get("stop_loss"),
                    target_price=thesis.get("target_price"),
                )
                result = await self._watchdog.assess(ctx)  # type: ignore[union-attr]
                if result is not None:
                    d = result.model_dump() if hasattr(result, "model_dump") else vars(result)
                    d["ticker"] = thesis["ticker"]
                    outputs.append(d)
            except Exception as exc:
                logger.warning(
                    "signal_engine_listener.watchdog_ticker_failed",
                    ticker=thesis.get("ticker"),
                    error=str(exc),
                )
        logger.info("signal_engine_listener.watchdog_done", count=len(outputs))
        return outputs

    async def _load_portfolio_data(self) -> dict | None:
        """Load portfolio P&L snapshot as dict for SignalEngineAgent."""
        try:
            from src.platform.bootstrap import get_pnl_service
            from src.platform.db import AsyncSessionLocal

            pnl_factory = get_pnl_service()
            async with AsyncSessionLocal() as session:
                pnl_svc = pnl_factory(session)
                pnl = await pnl_svc.get_portfolio_pnl(self._user_id)

            positions = []
            for pos in pnl.positions:
                positions.append({
                    "ticker": pos.ticker,
                    "weight_pct": getattr(pos, "weight_pct", None),
                    "pnl_pct": getattr(pos, "unrealized_pct", None),
                    "quantity": getattr(pos, "qty", None),
                    "last_verdict": None,  # enriched by SignalEngineAgent
                })
            result = {
                "positions": positions,
                "total_pnl_pct": getattr(pnl, "total_unrealized_pct", None),
                "position_count": len(positions),
            }
            logger.info("signal_engine_listener.portfolio_loaded", position_count=len(positions))
            return result
        except Exception as exc:
            logger.warning("signal_engine_listener.portfolio_failed", error=str(exc))
            return None

    async def _load_feedback_summary(self) -> str:
        """Load feedback calibration string from readmodel.DashboardService.

        Returns empty string when unavailable — never blocks pipeline.
        """
        try:
            from src.platform.db import AsyncSessionLocal
            from src.readmodel.dashboard_service import DashboardService

            async with AsyncSessionLocal() as session:
                summary = await DashboardService(session).get_brief_feedback_summary(
                    self._user_id
                )
            acted_rate = summary.get("acted_rate_30d")
            total = summary.get("total_feedbacks_30d", 0)
            if acted_rate is None or total < 10:
                return ""
            ignored_sectors = summary.get("ignored_sectors", [])
            regret_ignores = summary.get("regret_ignores", 0)
            return (
                f"acted_rate={acted_rate:.2f} | "
                f"ignored_sectors={ignored_sectors} | "
                f"regret_ignores={regret_ignores} | "
                f"total_events={total}"
            )
        except Exception as exc:
            logger.warning("signal_engine_listener.feedback_failed", error=str(exc))
            return ""

    async def _run_signal_engine(
        self,
        watchdog_outputs: list[dict],
        active_theses: list[dict],
        portfolio_data: dict | None,
        feedback_summary: str,
    ) -> object | None:
        try:
            output = await self._engine.run(  # type: ignore[union-attr]
                watchdog_outputs=watchdog_outputs,
                stress_outputs=[],  # Wave 3: wire StressTestAgent outputs
                active_theses=active_theses,
                portfolio_data=portfolio_data,
                feedback_summary=feedback_summary,
            )
            logger.info(
                "signal_engine_listener.engine_done",
                signals=len(getattr(output, "ranked_signals", [])),
                confidence=getattr(output, "confidence", None),
            )
            return output
        except Exception as exc:
            logger.warning("signal_engine_listener.engine_failed", error=str(exc))
            return None

    async def _emit_completed(self, output: object, event: "SignalEngineRequestedEvent") -> None:
        try:
            from src.platform.event_bus import get_event_bus
            from src.platform.events import SignalEngineCompletedEvent

            completed = SignalEngineCompletedEvent(
                signal_summary=getattr(output, "signal_summary", "") or "",
                ranked_signals_count=len(getattr(output, "ranked_signals", [])),
                thesis_review_triggers=getattr(output, "thesis_review_triggers", []) or [],
                confidence=getattr(output, "confidence", 0.0) or 0.0,
            )
            await get_event_bus().publish(completed)
            logger.info(
                "signal_engine_listener.completed_emitted",
                ranked_signals_count=completed.ranked_signals_count,
                thesis_review_triggers=len(completed.thesis_review_triggers),
            )
        except Exception as exc:
            logger.warning("signal_engine_listener.emit_failed", error=str(exc))

    async def _send_to_discord(self, output: object) -> None:
        """Send signal_summary to morning channel if Discord client is available."""
        if self._discord_client is None or self._morning_channel_id is None:
            return
        summary = getattr(output, "signal_summary", "") or ""
        if not summary:
            return
        try:
            channel = self._discord_client.get_channel(self._morning_channel_id)  # type: ignore[union-attr]
            if channel is None:
                logger.warning(
                    "signal_engine_listener.channel_not_found",
                    channel_id=self._morning_channel_id,
                )
                return
            signals = getattr(output, "ranked_signals", []) or []
            header = (
                f"🧠 **Signal Engine — {len(signals)} tín hiệu**\n"
                f"{'─' * 40}\n"
            )
            for chunk in _split_discord_message(header + summary):
                await channel.send(chunk)  # type: ignore[union-attr]
            logger.info(
                "signal_engine_listener.discord_sent",
                channel_id=self._morning_channel_id,
            )
        except Exception as exc:
            logger.warning("signal_engine_listener.discord_send_failed", error=str(exc))


def _split_discord_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ── singleton ─────────────────────────────────────────────────────────────────

_listener: SignalEngineListener | None = None


def get_signal_engine_listener(
    signal_engine_agent: object | None = None,
    watchdog_agent: object | None = None,
    session_factory: object | None = None,
    morning_channel_id: int | None = None,
    user_id: str | None = None,
) -> SignalEngineListener:
    """Return the global SignalEngineListener singleton.

    First call must provide all constructor args.
    Subsequent calls with no args return the cached instance.
    """
    global _listener
    if _listener is None:
        if signal_engine_agent is None or watchdog_agent is None or session_factory is None:
            raise RuntimeError(
                "SignalEngineListener not initialised — "
                "call get_signal_engine_listener(agent, watchdog, factory, ...) first."
            )
        _listener = SignalEngineListener(
            signal_engine_agent=signal_engine_agent,
            watchdog_agent=watchdog_agent,
            session_factory=session_factory,
            morning_channel_id=morning_channel_id,
            user_id=user_id,
        )
    return _listener
