"""TradeUseCase — single source of truth for buy/sell orchestration.

Owner: portfolio segment.

This use case sits between the domain services (PortfolioService,
DecisionService) and the adapter layer (API routes, Discord bot commands).
Neither adapter should contain orchestration logic — they call this use case
and map the result to their own response format.

Responsibilities:
  - Delegate the trade itself to PortfolioService.buy / PortfolioService.sell.
  - Fire-and-forget a DecisionLog entry via DecisionService when thesis_id
    is provided.
  - Auto-fill rationale when thesis_id is present but rationale is empty,
    so that every thesis-linked trade is always captured in the decision log
    regardless of whether the caller supplied a rationale string.
  - Fire-and-forget a ReplayAgent review after a SELL trade is committed
    (Wave: post-trade feedback loop). Never blocks the SELL response.
  - Return a structured TradeResult consumed by all adapters.

Failure contract:
  - Trade is always the source of truth. DecisionLog failure is soft —
    logged as WARNING, never re-raised, never blocks the trade response.
  - ReplayAgent dispatch failure is soft — logged as WARNING, never re-raised.

Adding a new channel (mobile app, webhook, scheduled rebalancer …):
  Import TradeUseCase and call execute_buy() / execute_sell().
  Do NOT duplicate orchestration in the new adapter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.portfolio.service import (
    InsufficientQtyError,
    PortfolioService,
    PositionNotFoundError,
)

logger = get_logger(__name__)

_AUTO_RATIONALE_TEMPLATE = "Quick trade: {decision_type} via {source}"


@dataclass
class TradeResult:
    """Structured result returned by TradeUseCase to all adapters."""

    trade_id: int
    position_id: int
    ticker: str
    trade_type: str                    # "buy" | "sell"
    qty: float
    price: float
    avg_cost: float
    position_qty: float
    realized_pnl: float | None
    position_closed: bool
    decision_logged: bool = field(default=False)


class TradeUseCase:
    """Orchestrates buy/sell trade execution and optional decision logging.

    Usage (API adapter)::

        uc = TradeUseCase(session=session, quote_service=quote_svc)
        result = await uc.execute_buy(
            user_id=user_id,
            ticker=body.ticker,
            qty=body.qty,
            price=body.price,
            thesis_id=body.thesis_id,
            rationale=body.rationale,
            sector=body.sector,
            note=body.note,
        )
        # map result → TradeResponse

    Usage (Discord bot adapter)::

        uc = TradeUseCase(session=session, quote_service=get_quote_service())
        result = await uc.execute_buy(
            user_id=user_id,
            ticker=ticker,
            qty=qty,
            price=price,
            thesis_id=thesis_id,
            rationale=rationale,
            source="discord",
        )
        # map result → Discord embed
    """

    def __init__(self, session: AsyncSession, quote_service: object) -> None:
        self._session = session
        self._quote_service = quote_service

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def execute_buy(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        thesis_id: int | None = None,
        rationale: str | None = None,
        sector: str | None = None,
        note: str | None = None,
        source: str = "dashboard",
    ) -> TradeResult:
        """Execute a BUY trade and optionally log a decision.

        Args:
            user_id:   Authenticated user identifier.
            ticker:    Stock ticker (will be uppercased).
            qty:       Number of shares to buy (> 0).
            price:     Execution price per share (> 0).
            thesis_id: Optional linked thesis — triggers DecisionLog creation.
            rationale: Optional decision rationale. If thesis_id is set but
                       rationale is empty, an auto-filled string is used so
                       the DecisionLog is always created.
            sector:    Optional sector label stored on the position.
            note:      Optional free-text note stored on the trade.
            source:    Caller identifier for auto-rationale wording
                       (e.g. "dashboard", "discord"). Default: "dashboard".

        Returns:
            TradeResult with decision_logged=True when a DecisionLog was
            successfully created.

        Raises:
            ValueError: qty or price is not positive (from PortfolioService).
        """
        svc = PortfolioService(self._session)
        position, trade = await svc.buy(
            user_id=user_id,
            ticker=ticker.upper().strip(),
            qty=qty,
            price=price,
            thesis_id=thesis_id,
            sector=sector,
            note=note,
        )

        decision_logged = await self._log_decision(
            user_id=user_id,
            ticker=trade.ticker,
            thesis_id=thesis_id,
            decision_type="BUY",
            rationale=rationale,
            execution_price=price,
            source=source,
        )

        return TradeResult(
            trade_id=trade.id,
            position_id=position.id,
            ticker=trade.ticker,
            trade_type="buy",
            qty=trade.qty,
            price=trade.price,
            avg_cost=position.avg_cost,
            position_qty=position.qty,
            realized_pnl=None,
            position_closed=False,
            decision_logged=decision_logged,
        )

    async def execute_sell(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        thesis_id: int | None = None,
        rationale: str | None = None,
        note: str | None = None,
        source: str = "dashboard",
    ) -> TradeResult:
        """Execute a SELL trade, log a decision, and fire ReplayAgent review.

        Args:
            user_id:   Authenticated user identifier.
            ticker:    Stock ticker (will be uppercased).
            qty:       Number of shares to sell (> 0).
            price:     Execution price per share (> 0).
            thesis_id: Optional linked thesis — triggers DecisionLog creation.
            rationale: Optional decision rationale. If thesis_id is set but
                       rationale is empty, an auto-filled string is used.
            note:      Optional free-text note stored on the trade.
            source:    Caller identifier for auto-rationale wording.

        Returns:
            TradeResult with decision_logged=True when a DecisionLog was
            successfully created.

        Raises:
            ValueError:            qty or price is not positive.
            PositionNotFoundError: No open position for this ticker.
            InsufficientQtyError:  sell qty > current position qty.
        """
        svc = PortfolioService(self._session)
        position, trade = await svc.sell(
            user_id=user_id,
            ticker=ticker.upper().strip(),
            qty=qty,
            price=price,
            note=note,
        )

        decision_logged = await self._log_decision(
            user_id=user_id,
            ticker=trade.ticker,
            thesis_id=thesis_id,
            decision_type="SELL",
            rationale=rationale,
            execution_price=price,
            source=source,
        )

        # Fire-and-forget ReplayAgent review — never blocks SELL response.
        # Runs as a background task; any failure is logged as WARNING only.
        self._dispatch_replay(
            user_id=user_id,
            trade=trade,
            position=position,
            thesis_id=thesis_id,
        )

        return TradeResult(
            trade_id=trade.id,
            position_id=position.id,
            ticker=trade.ticker,
            trade_type="sell",
            qty=trade.qty,
            price=trade.price,
            avg_cost=position.avg_cost,
            position_qty=position.qty,
            realized_pnl=trade.realized_pnl,
            position_closed=position.closed_at is not None,
            decision_logged=decision_logged,
        )

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _dispatch_replay(
        self,
        user_id: str,
        trade: object,
        position: object,
        thesis_id: int | None,
    ) -> None:
        """Schedule a ReplayAgent review as a fire-and-forget asyncio task.

        Contract:
          - Uses asyncio.create_task() — non-blocking, runs in the current
            event loop after execute_sell() returns to caller.
          - Session is passed directly into ReplayAgent; the task must NOT
            attempt to open a new session (it does not own the lifecycle).
          - Failure is logged as WARNING, never re-raised.
          - Only dispatched when event loop is running (guard via
            asyncio.get_event_loop().is_running()). Silent no-op otherwise
            (e.g. during sync tests).
        """
        try:
            trade_snapshot = {
                "id": getattr(trade, "id", None),
                "ticker": getattr(trade, "ticker", ""),
                "traded_at": str(getattr(trade, "traded_at", "")),
                "realized_pnl": getattr(trade, "realized_pnl", None),
                "price": getattr(trade, "price", None),
                "exit_reason": (
                    getattr(trade, "exit_reason").value
                    if getattr(trade, "exit_reason", None) is not None
                    else None
                ),
                "entry_signal_ref": getattr(trade, "entry_signal_ref", None),
                "thesis_id": thesis_id,
                "position_closed": getattr(position, "closed_at", None) is not None,
            }

            async def _run() -> None:
                try:
                    from src.ai.agents.replay_agent import ReplayAgent  # noqa: PLC0415
                    from src.ai.client import AIClient  # noqa: PLC0415

                    await ReplayAgent(AIClient()).run_for_trade(
                        session=self._session,
                        user_id=user_id,
                        trade_snapshot=trade_snapshot,
                        thesis_snapshot=None,  # enriched inside run_for_trade if thesis_id set
                    )
                    logger.info(
                        "portfolio.replay_agent_triggered",
                        user_id=user_id,
                        ticker=trade_snapshot["ticker"],
                        trade_id=trade_snapshot["id"],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "portfolio.replay_agent_task_failed",
                        user_id=user_id,
                        ticker=trade_snapshot.get("ticker"),
                        error=str(exc),
                    )

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_run())
            else:
                logger.debug(
                    "portfolio.replay_agent_skipped_no_loop",
                    user_id=user_id,
                )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "portfolio.replay_agent_dispatch_failed",
                user_id=user_id,
                error=str(exc),
            )

    async def _log_decision(
        self,
        user_id: str,
        ticker: str,
        thesis_id: int | None,
        decision_type: str,
        rationale: str | None,
        execution_price: float | None,
        source: str,
    ) -> bool:
        """Fire-and-forget decision log after a trade is persisted.

        Returns True if the DecisionLog was created successfully, False otherwise.
        Failure is always soft — logged as WARNING, never re-raised.

        Contract:
          - Only logs when thesis_id is provided.
          - User-supplied rationale is used as-is.
          - If rationale is missing, auto-fills using _AUTO_RATIONALE_TEMPLATE
            so every thesis-linked trade always generates a DecisionLog.
          - execution_price is forwarded so price_at_decision reflects the
            real fill price, not a live quote.
        """
        if not thesis_id:
            return False

        effective_rationale = rationale or _AUTO_RATIONALE_TEMPLATE.format(
            decision_type=decision_type,
            source=source,
        )

        if not rationale:
            logger.info(
                "portfolio.decision_log_auto_rationale",
                user_id=user_id,
                ticker=ticker,
                thesis_id=thesis_id,
                decision_type=decision_type,
                source=source,
                auto_rationale=effective_rationale,
            )

        try:
            from src.thesis.decision_service import DecisionService  # noqa: PLC0415

            svc = DecisionService(
                session=self._session,
                quote_service=self._quote_service,
            )
            await svc.log_decision(
                user_id=user_id,
                thesis_id=thesis_id,
                decision_type=decision_type,
                rationale=effective_rationale,
                execution_price=execution_price,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "portfolio.decision_log_failed",
                user_id=user_id,
                ticker=ticker,
                thesis_id=thesis_id,
                decision_type=decision_type,
                error=str(exc),
            )
            return False
