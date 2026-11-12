"""DerivSimService — giả lập HĐTL VN30 tự động theo base rate (paper trading).

Owner: portfolio segment. Quy tắc giao dịch ở market.deriv_sim (thuần); service này chỉ
đọc/ghi DB và lấy giá HĐ + tín hiệu từ market.derivatives.DerivativesService.

Luồng mỗi lần quét (bot gọi 5 phút/lần trong giờ giao dịch):
  tín hiệu VN30 + giá HĐ → market.deriv_sim.step → ghi lệnh mở/đóng → ghi tick P&L
  → trả danh sách sự kiện để bot gửi Discord (mở/đóng/stop/target/roll, cảnh báo margin).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from src.market.deriv_sim import (
    DEFAULT_FEE_PER_SIDE,
    SERIES_ORDER,
    SimParams,
    SimPosition,
    stats,
    step,
    trade_pnl,
)
from src.market.derivatives import initial_margin
from src.portfolio.models import DerivSimConfig, DerivSimTick, DerivSimTrade

FEE_PER_SIDE = DEFAULT_FEE_PER_SIDE


class DerivSimError(ValueError):
    pass


@dataclass(frozen=True)
class SimEvent:
    kind: str  # open | close | margin_alert
    side: str
    code: str
    qty: int
    price: float
    reason: str
    pnl: float | None = None  # đã trừ phí (close) hoặc chưa thực hiện (margin_alert)
    pnl_on_margin_pct: float | None = None


def _trade_dict(t: DerivSimTrade) -> dict[str, Any]:
    return {
        "id": t.id,
        "contract_code": t.contract_code,
        "side": t.side,
        "qty": t.qty,
        "entry_price": t.entry_price,
        "entry_at": t.entry_at,
        "entry_reason": t.entry_reason,
        "signal": t.signal,
        "exit_price": t.exit_price,
        "exit_at": t.exit_at,
        "exit_reason": t.exit_reason,
        "realized_pnl": t.realized_pnl,
    }


class DerivSimService:
    def __init__(self, session: Any, derivatives_service: Any) -> None:
        self._session = session
        self._deriv = derivatives_service

    # ── Cấu hình ────────────────────────────────────────────────────────────
    async def _config(self, user_id: str, create: bool = False) -> DerivSimConfig | None:
        cfg = await self._session.get(DerivSimConfig, user_id)
        if cfg is None and create:
            cfg = DerivSimConfig(
                user_id=user_id,
                active=False,
                series="F1M",
                qty=1,
                stop_points=15.0,
                target_points=30.0,
                alert_margin_pct=None,
            )
            self._session.add(cfg)
            await self._session.flush()
        return cfg

    async def _open_trade(self, user_id: str) -> DerivSimTrade | None:
        res = await self._session.execute(
            select(DerivSimTrade)
            .where(DerivSimTrade.user_id == user_id, DerivSimTrade.exit_at.is_(None))
            .order_by(DerivSimTrade.id.desc())
        )
        return res.scalars().first()

    async def update_config(
        self,
        user_id: str,
        *,
        active: bool | None = None,
        series: str | None = None,
        qty: int | None = None,
        stop_points: float | None = None,
        target_points: float | None = None,
        alert_margin_pct: float | None = None,
        clear_stop: bool = False,
        clear_target: bool = False,
        clear_alert: bool = False,
        now: datetime | None = None,
    ) -> DerivSimConfig:
        now = now or datetime.now(UTC)
        cfg = await self._config(user_id, create=True)
        assert cfg is not None
        if series is not None:
            if series not in SERIES_ORDER:
                raise DerivSimError(f"Kỳ hạn phải là một trong {', '.join(SERIES_ORDER)}.")
            cfg.series = series
        if qty is not None:
            if not 1 <= qty <= 1000:
                raise DerivSimError("Số HĐ phải từ 1 đến 1000.")
            cfg.qty = qty
        if stop_points is not None or clear_stop:
            cfg.stop_points = None if clear_stop else abs(float(stop_points or 0)) or None
        if target_points is not None or clear_target:
            cfg.target_points = None if clear_target else abs(float(target_points or 0)) or None
        if alert_margin_pct is not None or clear_alert:
            cfg.alert_margin_pct = (
                None if clear_alert else abs(float(alert_margin_pct or 0)) or None
            )
        if active is not None and active != cfg.active:
            cfg.active = active
            if active:
                cfg.started_at = now
                cfg.reentry_blocked_on = None
            else:
                await self._close_open(user_id, "stopped", now)
        cfg.updated_at = now
        await self._session.flush()
        return cfg

    async def _close_open(self, user_id: str, reason: str, now: datetime) -> None:
        """Dừng giả lập → đóng vị thế đang mở theo giá hiện tại (nếu có giá)."""
        trade = await self._open_trade(user_id)
        if trade is None:
            return
        snap = await self._deriv.contracts(now.date())
        px = next(
            (
                c.get("price")
                for c in snap.get("contracts", [])
                if c.get("code") == trade.contract_code
            ),
            None,
        )
        exit_px = float(px) if px is not None else trade.entry_price
        trade.exit_price = exit_px
        trade.exit_at = now
        trade.exit_reason = reason
        trade.realized_pnl = round(
            trade_pnl(trade.side, trade.qty, trade.entry_price, exit_px, FEE_PER_SIDE)
        )

    async def reset(self, user_id: str) -> None:
        """Xoá toàn bộ lệnh + tick giả lập (giữ cấu hình, tắt giả lập)."""
        from sqlalchemy import delete  # noqa: PLC0415

        await self._session.execute(delete(DerivSimTrade).where(DerivSimTrade.user_id == user_id))
        await self._session.execute(delete(DerivSimTick).where(DerivSimTick.user_id == user_id))
        cfg = await self._config(user_id)
        if cfg is not None:
            cfg.active = False
            cfg.reentry_blocked_on = None
            cfg.last_signal = None
        await self._session.flush()

    # ── Một lần quét ────────────────────────────────────────────────────────
    async def run_tick(self, user_id: str, now: datetime, today: date) -> list[SimEvent]:
        cfg = await self._config(user_id)
        if cfg is None or not cfg.active:
            return []
        snap = await self._deriv.contracts(today)
        contracts = list(snap.get("contracts", []))
        try:
            suggestion = getattr(await self._deriv.vn30_baserate(), "suggestion", None)
        except Exception:  # noqa: BLE001 — thiếu tiền lệ → giữ nguyên, không mở mới
            suggestion = None

        params = SimParams(
            series=cfg.series,
            qty=cfg.qty,
            stop_points=cfg.stop_points,
            target_points=cfg.target_points,
            fee_per_side=FEE_PER_SIDE,
        )
        trade = await self._open_trade(user_id)
        pos = (
            SimPosition(trade.contract_code, trade.side, trade.qty, trade.entry_price)
            if trade
            else None
        )
        blocked = cfg.reentry_blocked_on == today
        result = step(pos, suggestion, contracts, params, reentry_blocked=blocked)

        events: list[SimEvent] = []
        for a in result.actions:
            if a.kind == "close" and trade is not None:
                trade.exit_price = a.price
                trade.exit_at = now
                trade.exit_reason = a.reason
                trade.realized_pnl = round(
                    trade_pnl(trade.side, trade.qty, trade.entry_price, a.price, FEE_PER_SIDE)
                )
                events.append(
                    SimEvent("close", a.side, a.code, a.qty, a.price, a.reason, trade.realized_pnl)
                )
                trade = None
            elif a.kind == "open":
                trade = DerivSimTrade(
                    user_id=user_id,
                    contract_code=a.code,
                    side=a.side,
                    qty=a.qty,
                    entry_price=a.price,
                    entry_at=now,
                    entry_reason=a.reason,
                    signal=suggestion,
                )
                self._session.add(trade)
                events.append(SimEvent("open", a.side, a.code, a.qty, a.price, a.reason))
        if result.block_reentry:
            cfg.reentry_blocked_on = today

        # Tick P&L
        unreal = 0.0
        mark = result.mark
        if trade is not None and mark is not None:
            unreal = trade_pnl(trade.side, trade.qty, trade.entry_price, mark, FEE_PER_SIDE)
            margin = initial_margin(mark, trade.qty)
            pct = unreal / margin * 100 if margin else None
            if (
                cfg.alert_margin_pct
                and pct is not None
                and abs(pct) >= cfg.alert_margin_pct
                and not trade.margin_alerted
            ):
                trade.margin_alerted = True
                events.append(
                    SimEvent(
                        "margin_alert",
                        trade.side,
                        trade.contract_code,
                        trade.qty,
                        mark,
                        "margin",
                        round(unreal),
                        round(pct, 1),
                    )
                )
        cfg.last_signal = suggestion
        cfg.last_run_at = now
        await self._session.flush()
        if trade is not None and mark is None:
            # Đang có vị thế nhưng lần quét này không có giá HĐ → không ghi tick (tránh P&L = 0 giả)
            return events
        realized_total = await self._realized_total(user_id)
        self._session.add(
            DerivSimTick(
                user_id=user_id,
                at=now,
                contract_code=trade.contract_code if trade else None,
                side=trade.side if trade else None,
                price=mark,
                unrealized_pnl=round(unreal),
                realized_total=round(realized_total),
                signal=suggestion,
            )
        )
        await self._session.flush()
        return events

    async def _realized_total(self, user_id: str) -> float:
        res = await self._session.execute(
            select(DerivSimTrade.realized_pnl).where(
                DerivSimTrade.user_id == user_id, DerivSimTrade.realized_pnl.is_not(None)
            )
        )
        return float(sum(v for v in res.scalars().all() if v is not None))

    # ── Xem ─────────────────────────────────────────────────────────────────
    async def view(self, user_id: str, today: date, tick_limit: int = 600) -> dict[str, Any]:
        cfg = await self._config(user_id)
        trades_res = await self._session.execute(
            select(DerivSimTrade)
            .where(DerivSimTrade.user_id == user_id)
            .order_by(DerivSimTrade.id.desc())
            .limit(500)
        )
        trades = list(trades_res.scalars().all())
        open_t = next((t for t in trades if t.exit_at is None), None)
        ticks_res = await self._session.execute(
            select(DerivSimTick)
            .where(DerivSimTick.user_id == user_id)
            .order_by(DerivSimTick.at.desc())
            .limit(tick_limit)
        )
        ticks = list(reversed(ticks_res.scalars().all()))

        open_view: dict[str, Any] | None = None
        if open_t is not None:
            last = next(
                (k for k in reversed(ticks) if k.contract_code == open_t.contract_code), None
            )
            mark = last.price if last else None
            unreal = (
                trade_pnl(open_t.side, open_t.qty, open_t.entry_price, mark, FEE_PER_SIDE)
                if mark is not None
                else None
            )
            margin = initial_margin(mark or open_t.entry_price, open_t.qty)
            open_view = {
                **_trade_dict(open_t),
                "mark_price": mark,
                "unrealized_pnl": round(unreal) if unreal is not None else None,
                "initial_margin": round(margin),
                "pnl_on_margin_pct": round(unreal / margin * 100, 2)
                if unreal is not None and margin
                else None,
            }
        closed = [_trade_dict(t) for t in trades if t.exit_at is not None]
        return {
            "config": {
                "active": bool(cfg.active) if cfg else False,
                "series": cfg.series if cfg else "F1M",
                "qty": cfg.qty if cfg else 1,
                "stop_points": cfg.stop_points if cfg else 15.0,
                "target_points": cfg.target_points if cfg else 30.0,
                "alert_margin_pct": cfg.alert_margin_pct if cfg else None,
                "started_at": cfg.started_at if cfg else None,
                "last_run_at": cfg.last_run_at if cfg else None,
                "last_signal": cfg.last_signal if cfg else None,
                "reentry_blocked_today": bool(cfg and cfg.reentry_blocked_on == today),
                "fee_per_side": FEE_PER_SIDE,
            },
            "open": open_view,
            "trades": closed,
            "stats": stats(closed),
            "ticks": [
                {
                    "at": k.at,
                    "price": k.price,
                    "side": k.side,
                    "equity": round((k.realized_total or 0) + (k.unrealized_pnl or 0)),
                    "signal": k.signal,
                }
                for k in ticks
            ],
        }
