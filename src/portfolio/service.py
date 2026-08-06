"""PortfolioService — write-side lifecycle for Position, Trade, and DividendRecord.

Owner: portfolio segment.

Responsibilities:
  - buy()              — open new or add to existing position, record Trade(BUY)
  - sell()             — reduce or close position, record Trade(SELL) with realized P&L
  - correct_trade()    — fix price of a BUY trade and recalculate position avg_cost (VWAP)
  - record_dividend()  — record a cash or stock dividend received for a ticker
  - list_open()        — return all open positions for a user

Does NOT calculate P&L for display — that is PnlService (read concern).
Does NOT send Discord notifications — bot/adapter concern.

Partial sell:
  sell(qty < position.qty) reduces qty, keeps position open.
  sell(qty == position.qty) sets closed_at, position is fully closed.
  sell(qty > position.qty) raises InsufficientQtyError.

correct_trade():
  Only BUY trades can be corrected (SELL realized P&L is already settled).
  Recalculates position.avg_cost as VWAP from all BUY trades after correction.

record_dividend():
  Accepts cash (VND/share) or stock (ratio, e.g. 0.10 = 10%) dividends.
  Looks up open position to link position_id — position_id is nullable if
  ticker no longer has an open position (allowed for backdated entries).
  total_amount = qty * dividend_per_share (meaningful for cash; informational for stock).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.config import get_settings
from src.platform.logging import get_logger
from src.portfolio.models import DividendRecord, DividendType, Position, Trade, TradeType
from src.portfolio.repository import PortfolioRepository

logger = get_logger(__name__)

# Positions with qty below this threshold are treated as fully closed.
_QTY_ZERO_EPSILON = 1e-9


class InsufficientQtyError(Exception):
    """Raised when sell qty exceeds current position qty."""


class PositionNotFoundError(Exception):
    """Raised when no open position exists for the given ticker."""


class TradeNotFoundError(Exception):
    """Raised when trade_id does not exist or does not belong to user."""


class InvalidOperationError(Exception):
    """Raised when the requested operation is not valid for the trade/position state."""


class PortfolioService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        trade_fee_pct: float | None = None,
        sell_tax_pct: float | None = None,
    ) -> None:
        self._session = session
        self._repo = PortfolioRepository(session)
        # Wave 1: VN trading costs. Default from settings; injectable in tests.
        _s = get_settings()
        self._trade_fee_pct = trade_fee_pct if trade_fee_pct is not None else _s.trade_fee_pct
        self._sell_tax_pct = sell_tax_pct if sell_tax_pct is not None else _s.sell_tax_pct

    # ------------------------------------------------------------------
    # Buy
    # ------------------------------------------------------------------

    async def buy(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        thesis_id: int | None = None,
        note: str | None = None,
        sector: str | None = None,
    ) -> tuple[Position, Trade]:
        """Open a new position or add to an existing one.

        avg_cost is recalculated as volume-weighted average:
            new_avg = (old_qty * old_avg + qty * price) / (old_qty + qty)

        sector is an optional free-text label (e.g. "tài chính",
        "nguyên vật liệu"). Stored on Position for use by
        ContextBuilder._fetch_portfolio_bias(). Omit to leave unchanged
        on an existing position, or NULL on a new one.

        After saving the trade, if thesis_id is provided, backfills
        thesis.actual_entry_price on the first buy only (guards None check
        so avg-down trades do not overwrite the original entry price).

        Raises:
            ValueError: qty or price is not positive.

        Returns:
            (position, trade) — both flushed to DB, caller must commit.
        """
        if qty <= 0:
            raise ValueError(f"qty phải lớn hơn 0, nhận được: {qty}")
        if price <= 0:
            raise ValueError(f"price phải lớn hơn 0, nhận được: {price}")

        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)

        # Wave 1: buy fee is folded into cost basis so that realized PnL
        # (sell) and unrealized PnL (pnl_service) are net of acquisition
        # cost. effective_price = price * (1 + fee_pct).
        effective_price = price * (1 + self._trade_fee_pct)

        if position is None:
            position = Position(
                user_id=user_id,
                ticker=ticker,
                qty=qty,
                avg_cost=effective_price,
                sector=sector,
                thesis_id=thesis_id,
                note=note,
                opened_at=datetime.now(UTC),
            )
        else:
            # Recalculate VWAP avg_cost (net of buy fees on both legs)
            total_cost = position.qty * position.avg_cost + qty * effective_price
            position.qty += qty
            position.avg_cost = total_cost / position.qty
            if thesis_id is not None:
                position.thesis_id = thesis_id
            if sector is not None:
                position.sector = sector

        await self._repo.save_position(position)

        trade = Trade(
            user_id=user_id,
            ticker=ticker,
            position_id=position.id,
            trade_type=TradeType.BUY,
            qty=qty,
            price=price,
            realized_pnl=None,
            note=note,
            traded_at=datetime.now(UTC),
        )
        await self._repo.save_trade(trade)

        # Backfill actual_entry_price on the linked thesis (first buy only).
        await self._backfill_thesis_entry(thesis_id=thesis_id, actual_price=price, user_id=user_id)

        logger.info(
            "portfolio.bought",
            user_id=user_id,
            ticker=ticker,
            qty=qty,
            price=price,
            new_avg_cost=position.avg_cost,
            new_qty=position.qty,
            sector=sector,
            thesis_id=thesis_id,
        )
        return position, trade

    async def _backfill_thesis_entry(
        self,
        thesis_id: int | None,
        actual_price: float,
        user_id: str,
    ) -> None:
        """Set thesis.actual_entry_price on the first BUY execution.

        Only writes when:
          - thesis_id is not None
          - thesis belongs to user_id
          - thesis.actual_entry_price is currently None (first buy guard)

        Intentionally uses direct SA query (no ThesisService import) to
        avoid cross-segment dependency. This is a narrow, single-field
        write — acceptable at this boundary.
        """
        if thesis_id is None:
            return

        # Lazy import to keep the cross-segment surface minimal.
        from src.thesis.models import Thesis  # noqa: PLC0415

        result = await self._session.execute(
            select(Thesis).where(
                Thesis.id == thesis_id,
                Thesis.user_id == user_id,
            )
        )
        thesis = result.scalar_one_or_none()
        if thesis is not None and thesis.actual_entry_price is None:
            thesis.actual_entry_price = actual_price
            logger.info(
                "thesis.actual_entry_price_set",
                thesis_id=thesis_id,
                ticker=thesis.ticker,
                actual_entry_price=actual_price,
                entry_price=thesis.entry_price,
            )

    # ------------------------------------------------------------------
    # Sell
    # ------------------------------------------------------------------

    async def sell(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        note: str | None = None,
    ) -> tuple[Position, Trade]:
        """Reduce or close an open position.

        realized_pnl = (price - avg_cost) * qty

        Raises:
            ValueError:            qty or price is not positive.
            PositionNotFoundError: No open position for this ticker.
            InsufficientQtyError:  sell qty > current position qty.

        Returns:
            (position, trade) — position may be closed (closed_at set).
        """
        if qty <= 0:
            raise ValueError(f"qty phải lớn hơn 0, nhận được: {qty}")
        if price <= 0:
            raise ValueError(f"price phải lớn hơn 0, nhận được: {price}")

        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)

        if position is None:
            raise PositionNotFoundError(f"No open position for {ticker}")

        if qty > position.qty:
            raise InsufficientQtyError(
                f"Cannot sell {qty} of {ticker} — only {position.qty} held"
            )

        # Wave 1: net proceeds — sell fee + 0.1% sell tax deducted from
        # realized PnL so the number matches what the broker statement shows.
        realized_pnl = (price - position.avg_cost) * qty
        sell_costs = price * qty * (self._trade_fee_pct + self._sell_tax_pct)
        realized_pnl -= sell_costs
        position.realized_pnl += realized_pnl
        position.qty -= qty

        if position.qty <= _QTY_ZERO_EPSILON:
            position.qty = 0.0
            position.closed_at = datetime.now(UTC)

        await self._repo.save_position(position)

        trade = Trade(
            user_id=user_id,
            ticker=ticker,
            position_id=position.id,
            trade_type=TradeType.SELL,
            qty=qty,
            price=price,
            realized_pnl=realized_pnl,
            note=note,
            traded_at=datetime.now(UTC),
        )
        await self._repo.save_trade(trade)

        logger.info(
            "portfolio.sold",
            user_id=user_id,
            ticker=ticker,
            qty=qty,
            price=price,
            realized_pnl=realized_pnl,
            position_closed=position.closed_at is not None,
        )
        return position, trade

    # ------------------------------------------------------------------
    # Correct trade
    # ------------------------------------------------------------------

    async def correct_trade(
        self,
        user_id: str,
        trade_id: int,
        new_price: float,
    ) -> tuple[Position, Trade]:
        """Correct the price of a BUY trade and recalculate position avg_cost.

        Only BUY trades on open positions can be corrected.
        SELL trades are excluded because realized_pnl has already been settled
        and changing the sell price retroactively would distort accounting.

        Process:
          1. Load trade — verify ownership and trade_type == BUY.
          2. Verify the parent position is still open (closed_at is None).
          3. Update trade.price = new_price.
          4. Reload all BUY trades for the position and recalculate VWAP avg_cost.
          5. Save both trade and position.

        Raises:
            ValueError:            new_price is not positive.
            TradeNotFoundError:    trade_id not found or belongs to another user.
            InvalidOperationError: trade is not BUY, or position is already closed.

        Returns:
            (position, trade) — both flushed, caller must commit.
        """
        if new_price <= 0:
            raise ValueError(f"new_price phải lớn hơn 0, nhận được: {new_price}")

        trade = await self._repo.get_trade_by_id(trade_id)

        if trade is None or trade.user_id != user_id:
            raise TradeNotFoundError(f"Trade #{trade_id} not found.")

        if trade.trade_type != TradeType.BUY:
            raise InvalidOperationError(
                "Chỉ có thể sửa BUY trade. "
                "SELL trade đã được hạch toán realized P&L và không thể chỉnh sửa."
            )

        position = await self._repo.get_position_by_id(trade.position_id)
        if position is None or position.closed_at is not None:
            raise InvalidOperationError(
                f"Vị thế #{trade.position_id} đã đóng — không thể sửa trade thuộc vị thế đã closed."
            )

        old_price = trade.price
        trade.price = new_price
        await self._repo.save_trade(trade)

        buy_trades = await self._repo.list_buy_trades(position.id)
        total_cost = sum(t.price * t.qty for t in buy_trades)
        total_qty = sum(t.qty for t in buy_trades)
        position.avg_cost = total_cost / total_qty if total_qty > 0 else new_price
        await self._repo.save_position(position)

        logger.info(
            "portfolio.trade_corrected",
            user_id=user_id,
            trade_id=trade_id,
            ticker=trade.ticker,
            old_price=old_price,
            new_price=new_price,
            new_avg_cost=position.avg_cost,
        )
        return position, trade

    # ------------------------------------------------------------------
    # Stock split / stock dividend adjustment
    # ------------------------------------------------------------------

    async def apply_stock_split(
        self,
        user_id: str,
        ticker: str,
        ratio: float,
        reason: str = "split",
        note: str | None = None,
    ) -> tuple[Position, Trade]:
        """Apply a stock dividend / split: qty × (1 + ratio), avg_cost ÷ (1 + ratio).

        Cost-preserving: total cost basis (qty × avg_cost) không đổi — investor
        không giàu hơn hay nghèo đi sau sự kiện, chỉ có số lượng và giá vốn
        danh nghĩa thay đổi. Nếu không điều chỉnh, avg_cost cũ sẽ làm sai
        unrealized P&L, sizing, và stop-breach check sau ngày chốt quyền.

        VD: giữ 1,000 HPG avg 25,000 → chia cổ tức 15% → qty 1,150,
        avg_cost 21,739. Tổng vốn 25,000,000 không đổi.

        Audit trail: Trade(ADJUST, qty=bonus_shares, price=0, realized_pnl=None).
        reason: "stock_dividend" (thưởng cổ phiếu) | "split" (chia tách).
        Với stock_dividend, ghi kèm DividendRecord(STOCK) để dividend history
        phản ánh đúng sự kiện.

        Raises:
            ValueError: ratio <= 0.
            PositionNotFoundError: không có vị thế mở cho ticker.

        Returns:
            (position, adjust_trade) — flushed, caller must commit.
        """
        if ratio <= 0:
            raise ValueError(f"ratio phải lớn hơn 0, nhận được: {ratio}")
        if reason not in ("stock_dividend", "split"):
            raise ValueError(f"reason phải là 'stock_dividend' hoặc 'split', nhận được: {reason}")

        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)
        if position is None:
            raise PositionNotFoundError(f"No open position for {ticker}")

        old_qty = position.qty
        old_avg = position.avg_cost
        bonus_qty = old_qty * ratio
        position.qty = old_qty + bonus_qty
        position.avg_cost = old_avg / (1 + ratio)
        await self._repo.save_position(position)

        auto_note = (
            f"{'Cổ tức cổ phiếu' if reason == 'stock_dividend' else 'Chia tách'} "
            f"{ratio:.0%}: {old_qty:,.0f} → {position.qty:,.0f} cp, "
            f"giá vốn {old_avg:,.0f} → {position.avg_cost:,.0f}"
        )
        trade = Trade(
            user_id=user_id,
            ticker=ticker,
            position_id=position.id,
            trade_type=TradeType.ADJUST,
            qty=bonus_qty,
            price=0.0,          # không phải giao dịch tiền — price=0 để không nhiễu VWAP
            realized_pnl=None,
            note=f"{auto_note}{(' — ' + note) if note else ''}",
            traded_at=datetime.now(UTC),
        )
        await self._repo.save_trade(trade)

        if reason == "stock_dividend":
            dividend = DividendRecord(
                user_id=user_id,
                ticker=ticker,
                position_id=position.id,
                qty=old_qty,
                dividend_per_share=ratio,   # tỷ lệ, VD 0.15 = 15%
                total_amount=bonus_qty,     # số cp thưởng nhận được
                dividend_type=DividendType.STOCK,
                note=note,
                paid_at=datetime.now(UTC),
            )
            await self._repo.save_dividend(dividend)

        logger.info(
            "portfolio.stock_split_applied",
            user_id=user_id,
            ticker=ticker,
            reason=reason,
            ratio=ratio,
            old_qty=old_qty,
            new_qty=position.qty,
            old_avg_cost=old_avg,
            new_avg_cost=position.avg_cost,
        )
        return position, trade

    # ------------------------------------------------------------------
    # Direct position edit (manual correction)
    # ------------------------------------------------------------------

    async def edit_position(
        self,
        user_id: str,
        ticker: str,
        qty: float | None = None,
        avg_cost: float | None = None,
    ) -> Position:
        """Sửa trực tiếp qty và/hoặc giá vốn của vị thế đang mở — không audit trail.

        Dùng cho: nhập sai lúc đầu, sync với tài khoản chứng khoán thật,
        hoặc điều chỉnh tay ngoài luồng buy/sell/adjust chuẩn.

        Khác với apply_stock_split(): ở đây người dùng tự chịu trách nhiệm
        về tính đúng đắn của cặp (qty, avg_cost) mới — hệ thống không kiểm
        cost-preserving, không ghi Trade record, không tính realized P&L.

        Args:
            qty:      số lượng mới (None = giữ nguyên). Phải > 0.
            avg_cost: giá vốn mới (None = giữ nguyên). Phải > 0.

        Raises:
            ValueError: không có trường nào được sửa, hoặc giá trị <= 0.
            PositionNotFoundError: không có vị thế mở cho ticker.

        Returns:
            Position đã update (flushed; caller must commit).
        """
        if qty is None and avg_cost is None:
            raise ValueError("Phải truyền ít nhất một trong qty hoặc avg_cost")
        if qty is not None and qty <= 0:
            raise ValueError(f"qty phải lớn hơn 0, nhận được: {qty}")
        if avg_cost is not None and avg_cost <= 0:
            raise ValueError(f"avg_cost phải lớn hơn 0, nhận được: {avg_cost}")

        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)
        if position is None:
            raise PositionNotFoundError(f"No open position for {ticker}")

        old_qty, old_avg = position.qty, position.avg_cost
        if qty is not None:
            position.qty = qty
        if avg_cost is not None:
            position.avg_cost = avg_cost
        await self._repo.save_position(position)

        # Audit trail: PositionEdit lưu cả giá trị cũ lẫn mới để truy vết.
        # GET /portfolio/trades merge records này vào timeline cùng trades.
        from src.portfolio.models import PositionEdit

        await self._repo.save_position_edit(
            PositionEdit(
                user_id=user_id,
                ticker=ticker,
                position_id=position.id,
                old_qty=old_qty,
                new_qty=position.qty,
                old_avg_cost=old_avg,
                new_avg_cost=position.avg_cost,
                edited_at=datetime.now(UTC),
            )
        )

        logger.info(
            "portfolio.position_edited",
            user_id=user_id,
            ticker=ticker,
            old_qty=old_qty,
            new_qty=position.qty,
            old_avg_cost=old_avg,
            new_avg_cost=position.avg_cost,
        )
        return position

    # ------------------------------------------------------------------
    # Dividend
    # ------------------------------------------------------------------

    async def record_dividend(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        dividend_per_share: float,
        dividend_type: DividendType = DividendType.CASH,
        ex_date: datetime | None = None,
        note: str | None = None,
    ) -> DividendRecord:
        """Record a dividend received for a ticker.

        total_amount = qty * dividend_per_share.
        For cash dividends: dividend_per_share is VND per share.
        For stock dividends: dividend_per_share is the ratio (e.g. 0.10 = 10%).

        Automatically links to the open position if one exists.
        position_id is nullable — recording against a closed position is allowed.

        Raises:
            ValueError: qty or dividend_per_share is not positive.

        Returns:
            DividendRecord — flushed to DB, caller must commit.
        """
        if qty <= 0:
            raise ValueError(f"qty phải lớn hơn 0, nhận được: {qty}")
        if dividend_per_share <= 0:
            raise ValueError(f"dividend_per_share phải lớn hơn 0, nhận được: {dividend_per_share}")

        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)
        position_id = position.id if position is not None else None
        total_amount = qty * dividend_per_share

        record = DividendRecord(
            user_id=user_id,
            ticker=ticker,
            position_id=position_id,
            qty=qty,
            dividend_per_share=dividend_per_share,
            total_amount=total_amount,
            dividend_type=dividend_type,
            ex_date=ex_date,
            note=note,
            paid_at=datetime.now(UTC),
        )
        await self._repo.save_dividend(record)

        logger.info(
            "portfolio.dividend_recorded",
            user_id=user_id,
            ticker=ticker,
            qty=qty,
            dividend_per_share=dividend_per_share,
            dividend_type=dividend_type.value,
            total_amount=total_amount,
            position_id=position_id,
        )
        return record

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_open(self, user_id: str) -> list[Position]:
        """Return all open positions, ordered by ticker."""
        return await self._repo.list_open_positions(user_id)
