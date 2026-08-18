"""PositionSizingService — quantitative position sizing for pre-trade checks.

Owner: portfolio segment.

Formula (fixed-fractional risk sizing):
    risk_budget_vnd = equity × risk_per_trade_pct
    max_qty         = risk_budget_vnd / (entry_price − stop_price)
    capped by:
      - max_position_pct of equity   (concentration limit)
      - available cash estimate      (liquidity limit)

Equity model (single-user, manual-entry portfolio — no broker feed):
    equity = total_market_value(open positions) + cash_estimate
    cash_estimate = portfolio_cash_vnd from settings when > 0,
                    otherwise falls back to realized_pnl sum + dividends
                    (documented, visible in output via cash_known flag).

Stop price resolution order:
    1. thesis.stop_loss of the active thesis for (user, ticker)
    2. fallback: entry_price × (1 − default_stop_loss_pct)

Also enforces two Livermore risk invariants (read-only lookups against
portfolio's own Position table + watchlist's SignalEvent table):
  - Wave 8.2: averaging-down guard — HARD block (max_qty=0) when entry_price
    is below the open position's avg_cost. Never rescue a loser by buying more.
  - Wave 8.3: pyramiding discipline — ADVISORY only (pyramiding_note) when
    adding to a WINNING position without a recent BREAKOUT signal to justify
    the add. Warns, does not block — confidence-driven adds aren't a hard
    financial risk the way averaging down is, so this stays a nudge.

Does NOT:
  - execute or block trades beyond the averaging-down hard gate above
    (verdict text otherwise owns GO/AVOID)
  - own risk_appetite parsing beyond the numeric knobs in settings
  - fetch quotes itself (caller passes current price)

Output is a plain dataclass consumed by thesis.PreTradeService and rendered
by bot adapter. All numbers in VND.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.config import get_settings
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Lot size on HOSE/HNX is 100 shares; UPCoM trades odd lots but 100 is the
# practical unit for retail sizing.
_LOT_SIZE = 100


@dataclass
class SizingResult:
    """Quantitative sizing output attached to a pre-trade check."""

    ticker: str
    entry_price: float
    stop_price: float
    stop_source: str              # "thesis" | "fallback_default"
    equity_vnd: float
    cash_known: bool              # False when cash is estimated without config
    risk_per_trade_pct: float
    risk_budget_vnd: float        # equity × risk_per_trade_pct
    max_qty: int                  # final answer after all caps, lot-rounded
    max_value_vnd: float          # max_qty × entry_price
    portfolio_pct_after: float    # max_value_vnd / equity × 100
    cap_reason: str               # which cap bound the size: "risk" | "concentration" | "cash" | "invalid" | "averaging_down_blocked"
    warnings: list[str] = field(default_factory=list)
    pyramiding_note: str = ""     # Wave 8.3 — advisory only, never blocks sizing

    def to_note(self) -> str:
        """Render compact Vietnamese block for embed/prompt consumption."""
        if self.cap_reason == "averaging_down_blocked":
            return "\u26d4 " + "; ".join(self.warnings)
        if self.cap_reason == "invalid":
            return "Không tính được sizing — " + "; ".join(self.warnings)
        lines = [
            f"Tối đa **{self.max_qty:,} cp** (~{self.max_value_vnd:,.0f}đ, "
            f"{self.portfolio_pct_after:.1f}% NAV)",
            f"Stop {self.stop_price:,.0f}đ ({self.stop_source}) → rủi ro "
            f"{self.risk_budget_vnd:,.0f}đ ({self.risk_per_trade_pct:.1%} NAV)",
        ]
        if self.cap_reason == "concentration":
            lines.append("Bị giới hạn bởi ngưỡng tập trung, không phải stop loss")
        elif self.cap_reason == "cash":
            lines.append("Bị giới hạn bởi tiền mặt khả dụng")
        if not self.cash_known:
            lines.append("_(tiền mặt ước tính — set PORTFOLIO_CASH_VND trong .env để chính xác)_")
        if self.pyramiding_note:
            lines.append(f"⚠️ {self.pyramiding_note}")
        return "\n".join(lines)


class PositionSizingService:
    """Compute max position size for a prospective trade.

    Deps: session only. Reads portfolio positions (own segment) and the
    active thesis stop_loss (thesis segment, read-only via repository).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _knobs(self) -> tuple[float, float, float, float, float]:
        """Read settings at call time (not __init__) so tests and runtime
        .env overrides apply without reconstructing the service."""
        s = get_settings()
        return (
            s.risk_per_trade_pct,
            s.max_position_pct,
            s.default_stop_loss_pct,
            s.portfolio_cash_vnd,
            s.trade_fee_pct,
        )

    async def size_for_entry(
        self,
        *,
        user_id: str,
        ticker: str,
        entry_price: float,
    ) -> SizingResult:
        ticker = ticker.upper().strip()
        (
            risk_per_trade_pct,
            max_position_pct,
            default_stop_loss_pct,
            portfolio_cash_vnd,
            trade_fee_pct,
        ) = self._knobs()

        if entry_price <= 0:
            return SizingResult(
                ticker=ticker, entry_price=entry_price, stop_price=0.0,
                stop_source="invalid", equity_vnd=0.0, cash_known=False,
                risk_per_trade_pct=risk_per_trade_pct, risk_budget_vnd=0.0,
                max_qty=0, max_value_vnd=0.0, portfolio_pct_after=0.0,
                cap_reason="invalid",
                warnings=[f"entry_price={entry_price} không hợp lệ"],
            )

        # 0. Averaging-down guard (Wave 8.2 — Livermore: don't rescue a losing
        # position by buying more of it). Short-circuits before risk/stop math
        # since there is nothing to size — the entry itself is blocked.
        avg_down_block = await self._check_averaging_down(
            user_id=user_id, ticker=ticker, entry_price=entry_price,
            risk_per_trade_pct=risk_per_trade_pct,
        )
        if avg_down_block is not None:
            return avg_down_block

        # 0b. Pyramiding discipline check (Wave 8.3 — Livermore: pyramid only
        # at NEW pivotal points, never out of confidence from an existing
        # gain). Advisory only — never blocks, only annotates the note.
        pyramiding_note = await self._check_pyramiding_discipline(
            user_id=user_id, ticker=ticker, entry_price=entry_price
        )

        # 1. Stop price: thesis stop_loss → fallback default %
        stop_price, stop_source = await self._resolve_stop(user_id, ticker, entry_price)

        warnings: list[str] = []
        if stop_price >= entry_price:
            # Degenerate stop (thesis stop above current price) → no valid sizing
            return SizingResult(
                ticker=ticker, entry_price=entry_price, stop_price=stop_price,
                stop_source=stop_source, equity_vnd=0.0, cash_known=False,
                risk_per_trade_pct=risk_per_trade_pct, risk_budget_vnd=0.0,
                max_qty=0, max_value_vnd=0.0, portfolio_pct_after=0.0,
                cap_reason="invalid",
                warnings=[
                    f"stop_price ({stop_price:,.0f}) >= entry ({entry_price:,.0f}) — "
                    "kiểm tra lại stop_loss của thesis"
                ],
            )

        # 2. Equity + cash
        equity, cash, cash_known = await self._estimate_equity_and_cash(user_id)
        if not cash_known:
            warnings.append("cash ước tính từ realized PnL — chưa có PORTFOLIO_CASH_VND")

        # 3. Risk-based qty
        per_share_risk = entry_price - stop_price
        risk_budget = equity * risk_per_trade_pct
        qty_by_risk = risk_budget / per_share_risk

        # 4. Concentration cap
        max_value_by_concentration = equity * max_position_pct
        qty_by_concentration = max_value_by_concentration / entry_price

        # 5. Cash cap (include buy fee so the fill doesn't exceed cash)
        qty_by_cash = cash / (entry_price * (1 + trade_fee_pct)) if cash > 0 else 0.0

        qty = min(qty_by_risk, qty_by_concentration, qty_by_cash)
        if qty == qty_by_risk:
            cap_reason = "risk"
        elif qty == qty_by_concentration:
            cap_reason = "concentration"
        else:
            cap_reason = "cash"

        max_qty = int(qty // _LOT_SIZE * _LOT_SIZE)
        max_value = max_qty * entry_price

        result = SizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_price=stop_price,
            stop_source=stop_source,
            equity_vnd=equity,
            cash_known=cash_known,
            risk_per_trade_pct=risk_per_trade_pct,
            risk_budget_vnd=risk_budget,
            max_qty=max_qty,
            max_value_vnd=max_value,
            portfolio_pct_after=(max_value / equity * 100) if equity > 0 else 0.0,
            cap_reason=cap_reason,
            warnings=warnings,
            pyramiding_note=pyramiding_note,
        )
        logger.info(
            "position_sizing.computed",
            ticker=ticker, user_id=user_id,
            equity=round(equity), cash_known=cash_known,
            stop=stop_price, stop_source=stop_source,
            max_qty=max_qty, cap=cap_reason,
        )
        return result

    # ------------------------------------------------------------------

    async def _check_averaging_down(
        self, *, user_id: str, ticker: str, entry_price: float, risk_per_trade_pct: float,
    ) -> SizingResult | None:
        """Block sizing when this BUY would average down an open losing position.

        Livermore rule 2: never add to a losing position hoping it recovers.
        Returns None when there is no open position, avg_cost is unusable, or
        entry_price is at/above avg_cost (adding to a WINNING position is fine
        and stays subject to the normal risk/concentration/cash caps below).
        Read-only lookup via PortfolioRepository (portfolio's own segment) —
        never raises; any lookup failure is treated as "no position found"
        so a transient error never blocks a legitimate first-time entry.
        """
        try:
            from src.portfolio.repository import PortfolioRepository

            position = await PortfolioRepository(self._session).get_open_position(
                user_id, ticker
            )
        except Exception as exc:
            logger.warning(
                "position_sizing.averaging_down_lookup_failed",
                ticker=ticker, user_id=user_id, error=str(exc),
            )
            return None

        if position is None or position.avg_cost <= 0 or entry_price >= position.avg_cost:
            return None

        return SizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_price=0.0,
            stop_source="averaging_down_blocked",
            equity_vnd=0.0,
            cash_known=False,
            risk_per_trade_pct=risk_per_trade_pct,
            risk_budget_vnd=0.0,
            max_qty=0,
            max_value_vnd=0.0,
            portfolio_pct_after=0.0,
            cap_reason="averaging_down_blocked",
            warnings=[
                f"Đang giữ {position.qty:,.0f} {ticker} với giá vốn TB "
                f"{position.avg_cost:,.0f}đ, giá hiện tại {entry_price:,.0f}đ thấp hơn — "
                "mua thêm là bình quân giá xuống. Livermore: đừng cứu vị thế thua "
                "bằng cách mua thêm, hãy cắt lỗ hoặc chờ giá vượt lại giá vốn."
            ],
        )

    async def _check_pyramiding_discipline(
        self, *, user_id: str, ticker: str, entry_price: float
    ) -> str:
        """Warn (never block) when adding to a WINNING position without a
        fresh breakout signal to justify it.

        Livermore rule: pyramid only at NEW pivotal points, never just
        because a position is already up and it feels safe to add. Returns
        "" when there's no open position, the position isn't winning yet,
        a recent BREAKOUT signal justifies the add, or any lookup fails
        (fail-open — this is advisory text only, must never block a valid
        entry the way _check_averaging_down does).
        """
        try:
            from src.portfolio.repository import PortfolioRepository
            from src.watchlist.repository import WatchlistRepository
            from src.watchlist.signal_engine import SignalType

            position = await PortfolioRepository(self._session).get_open_position(
                user_id, ticker
            )
            if position is None or position.avg_cost <= 0 or entry_price <= position.avg_cost:
                return ""

            has_breakout = await WatchlistRepository(self._session).has_recent_signal(
                ticker, SignalType.BREAKOUT, hours=48, user_id=user_id
            )
            if has_breakout:
                return ""

            return (
                f"Mua thêm {ticker} khi đang lãi (giá {entry_price:,.0f}đ > vốn TB "
                f"{position.avg_cost:,.0f}đ) nhưng không có tín hiệu breakout mới trong "
                "48h — kiểm tra lại đây có phải pivotal point thật hay chỉ vì đang lãi nên tự tin mua thêm."
            )
        except Exception as exc:
            logger.warning(
                "position_sizing.pyramiding_check_failed",
                ticker=ticker, user_id=user_id, error=str(exc),
            )
            return ""

    async def _resolve_stop(
        self, user_id: str, ticker: str, entry_price: float
    ) -> tuple[float, str]:
        """Stop from active thesis; fallback entry × (1 − default_stop_loss_pct)."""
        try:
            from src.thesis.repository import ThesisRepository

            thesis = await ThesisRepository(self._session).get_active_by_user_and_ticker(
                user_id=user_id, ticker=ticker
            )
            if thesis is not None and thesis.stop_loss is not None and thesis.stop_loss > 0:
                return float(thesis.stop_loss), "thesis"
        except Exception as exc:
            logger.warning(
                "position_sizing.thesis_stop_lookup_failed",
                ticker=ticker, error=str(exc),
            )
        _, _, default_stop_loss_pct, _, _ = self._knobs()
        return entry_price * (1 - default_stop_loss_pct), "fallback_default"

    async def _estimate_equity_and_cash(
        self, user_id: str
    ) -> tuple[float, float, bool]:
        """Equity = market value of open positions + cash.

        Cash source:
          - settings.portfolio_cash_vnd when > 0 (owner-maintained) → cash_known=True
          - else: sum(realized_pnl over positions) + cash dividends → cash_known=False

        Position market value uses avg_cost when no live quote is available
        (sizing must work outside market hours without a quote dependency —
        the entry_price passed by the caller is the live price that matters).
        """
        from src.portfolio.repository import PortfolioRepository

        repo = PortfolioRepository(self._session)
        positions = await repo.list_open_positions(user_id)

        invested = sum(p.avg_cost * p.qty for p in positions)
        realized = sum(p.realized_pnl for p in positions)

        dividends = 0.0
        try:
            dividends = await repo.get_dividend_total(user_id)
        except Exception as exc:
            logger.warning(
                "position_sizing.dividend_total_failed", user_id=user_id, error=str(exc)
            )

        _, _, _, portfolio_cash_vnd, _ = self._knobs()
        if portfolio_cash_vnd > 0:
            cash = portfolio_cash_vnd
            cash_known = True
        else:
            cash = max(0.0, realized + dividends)
            cash_known = False

        equity = invested + cash
        if equity <= 0:
            # Fresh account with no positions and no cash config — use cash as
            # equity so sizing still produces a number instead of zeros.
            equity = cash if cash > 0 else 0.0

        return equity, cash, cash_known
