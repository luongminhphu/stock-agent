"""DerivSimService — giả lập HĐTL VN30 tự động theo base rate (paper trading).

Owner: portfolio segment. Quy tắc giao dịch ở market.deriv_sim (thuần); service này chỉ
đọc/ghi DB và lấy giá HĐ + tín hiệu từ market.derivatives.DerivativesService.

Luồng mỗi lần quét (bot gọi 5 phút/lần trong giờ giao dịch):
  tín hiệu VN30 + giá HĐ + ATR/biến động phiên → market.deriv_sim.step cho HAI sổ
  (base = theo tín hiệu, fade = đánh ngược) → ghi lệnh mở/đóng → ghi tick P&L từng sổ
  → trả sự kiện; ``SimEvent.primary`` đánh dấu sổ chính (theo ``mode``) để bot chỉ gửi
  Discord cho sổ đó.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from src.market.deriv_sim import (
    BOOKS,
    DEFAULT_FEE_PER_SIDE,
    MODES,
    SERIES_ORDER,
    SimParams,
    SimPosition,
    choose_primary,
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
    strategy: str = "base"
    primary: bool = True  # sổ chính theo mode → bot gửi Discord
    stop_price: float | None = None
    target_price: float | None = None


def _trade_dict(t: DerivSimTrade) -> dict[str, Any]:
    return {
        "id": t.id,
        "strategy": t.strategy,
        "contract_code": t.contract_code,
        "side": t.side,
        "qty": t.qty,
        "entry_price": t.entry_price,
        "stop_price": t.stop_price,
        "target_price": t.target_price,
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

    async def active_user_ids(self) -> list[str]:
        """Người dùng đang bật giả lập (bot quét cho từng người)."""
        res = await self._session.execute(
            select(DerivSimConfig.user_id).where(DerivSimConfig.active.is_(True))
        )
        return [str(u) for u in res.scalars().all()]

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
                mode="follow",
                avoid_round=True,
                crowd_atr=0.8,
            )
            self._session.add(cfg)
            await self._session.flush()
        return cfg

    async def _open_trade(self, user_id: str, strategy: str = "base") -> DerivSimTrade | None:
        res = await self._session.execute(
            select(DerivSimTrade)
            .where(
                DerivSimTrade.user_id == user_id,
                DerivSimTrade.strategy == strategy,
                DerivSimTrade.exit_at.is_(None),
            )
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
        mode: str | None = None,
        stop_atr: float | None = None,
        target_atr: float | None = None,
        avoid_round: bool | None = None,
        crowd_atr: float | None = None,
        clear_stop_atr: bool = False,
        clear_target_atr: bool = False,
        clear_crowd: bool = False,
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
        if mode is not None:
            if mode not in MODES:
                raise DerivSimError(f"Chế độ phải là một trong {', '.join(MODES)}.")
            cfg.mode = mode
        if stop_atr is not None or clear_stop_atr:
            cfg.stop_atr = None if clear_stop_atr else abs(float(stop_atr or 0)) or None
        if target_atr is not None or clear_target_atr:
            cfg.target_atr = None if clear_target_atr else abs(float(target_atr or 0)) or None
        if crowd_atr is not None or clear_crowd:
            cfg.crowd_atr = None if clear_crowd else abs(float(crowd_atr or 0)) or None
        if avoid_round is not None:
            cfg.avoid_round = avoid_round
        if active is not None and active != cfg.active:
            cfg.active = active
            if active:
                cfg.started_at = now
                cfg.reentry_blocked_on = None
                cfg.fade_reentry_blocked_on = None
            else:
                await self._close_open(user_id, "stopped", now)
        cfg.updated_at = now
        await self._session.flush()
        return cfg

    async def _close_open(self, user_id: str, reason: str, now: datetime) -> None:
        """Dừng giả lập → đóng vị thế đang mở của cả hai sổ theo giá hiện tại (nếu có giá)."""
        trades = [t for b in BOOKS if (t := await self._open_trade(user_id, b)) is not None]
        if not trades:
            return
        snap = await self._deriv.contracts(now.date())
        prices = {c.get("code"): c.get("price") for c in snap.get("contracts", [])}
        for trade in trades:
            px = prices.get(trade.contract_code)
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
            cfg.fade_reentry_blocked_on = None
            cfg.last_signal = None
        await self._session.flush()

    # ── Một lần quét ────────────────────────────────────────────────────────
    def _params(self, cfg: DerivSimConfig) -> SimParams:
        return SimParams(
            series=cfg.series,
            qty=cfg.qty,
            stop_points=cfg.stop_points,
            target_points=cfg.target_points,
            fee_per_side=FEE_PER_SIDE,
            stop_atr=cfg.stop_atr,
            target_atr=cfg.target_atr,
            avoid_round=bool(cfg.avoid_round),
            crowd_atr=cfg.crowd_atr,
        )

    async def _primary(self, user_id: str, mode: str) -> str:
        if mode != "auto":
            return choose_primary(mode, {}, {})
        closed = await self._closed(user_id)
        return choose_primary(
            mode,
            stats([t for t in closed if t["strategy"] == "base"]),
            stats([t for t in closed if t["strategy"] == "fade"]),
        )

    async def run_tick(self, user_id: str, now: datetime, today: date) -> list[SimEvent]:
        cfg = await self._config(user_id)
        if cfg is None or not cfg.active:
            return []
        snap = await self._deriv.contracts(today)
        contracts = list(snap.get("contracts", []))
        edge: float | None = None
        try:
            rep = await self._deriv.vn30_baserate()
            suggestion = getattr(rep, "suggestion", None)
            # Độ lệch của tầm quyết định tín hiệu → vùng đệm khi đang giữ lệnh
            h = getattr(self._deriv, "SIGNAL_HORIZON", None)
            out = (getattr(rep, "outcomes", None) or {}).get(h) if h is not None else None
            edge = getattr(out, "edge", None)
        except Exception:  # noqa: BLE001 — thiếu tiền lệ → giữ nguyên, không mở mới
            suggestion = None
        ctx: dict[str, Any] = {}
        if hasattr(self._deriv, "sim_context"):
            try:
                ctx = dict(await self._deriv.sim_context(today))
            except Exception:  # noqa: BLE001 — thiếu ATR → Stop/Target theo điểm, tắt lọc
                ctx = {}
        params = self._params(cfg)
        primary = await self._primary(user_id, cfg.mode or "follow")

        events: list[SimEvent] = []
        for book in BOOKS:
            ctx_book = {**ctx, "edge": edge}
            events += await self._run_book(
                cfg,
                user_id,
                book,
                book == primary,
                suggestion,
                contracts,
                params,
                ctx_book,
                now,
                today,
            )
        cfg.last_signal = suggestion
        cfg.last_run_at = now
        await self._session.flush()
        return events

    async def _run_book(
        self,
        cfg: DerivSimConfig,
        user_id: str,
        book: str,
        primary: bool,
        suggestion: str | None,
        contracts: list[dict[str, Any]],
        params: SimParams,
        ctx: dict[str, Any],
        now: datetime,
        today: date,
    ) -> list[SimEvent]:
        fade = book == "fade"
        trade = await self._open_trade(user_id, book)
        pos = (
            SimPosition(
                trade.contract_code,
                trade.side,
                trade.qty,
                trade.entry_price,
                trade.stop_price,
                trade.target_price,
            )
            if trade
            else None
        )
        blocked_on = cfg.fade_reentry_blocked_on if fade else cfg.reentry_blocked_on
        result = step(
            pos,
            suggestion,
            contracts,
            params,
            reentry_blocked=blocked_on == today,
            fade=fade,
            atr=ctx.get("atr"),
            move=ctx.get("move"),
            edge=ctx.get("edge"),
        )

        def ev(kind: str, **kw: Any) -> SimEvent:
            return SimEvent(kind, strategy=book, primary=primary, **kw)

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
                    ev(
                        "close",
                        side=a.side,
                        code=a.code,
                        qty=a.qty,
                        price=a.price,
                        reason=a.reason,
                        pnl=trade.realized_pnl,
                    )
                )
                trade = None
            elif a.kind == "open":
                trade = DerivSimTrade(
                    user_id=user_id,
                    strategy=book,
                    contract_code=a.code,
                    side=a.side,
                    qty=a.qty,
                    entry_price=a.price,
                    stop_price=a.stop_price,
                    target_price=a.target_price,
                    entry_at=now,
                    entry_reason=a.reason,
                    signal=suggestion,
                    margin_alerted=False,
                )
                self._session.add(trade)
                events.append(
                    ev(
                        "open",
                        side=a.side,
                        code=a.code,
                        qty=a.qty,
                        price=a.price,
                        reason=a.reason,
                        stop_price=a.stop_price,
                        target_price=a.target_price,
                    )
                )
        if result.block_reentry:
            if fade:
                cfg.fade_reentry_blocked_on = today
            else:
                cfg.reentry_blocked_on = today

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
                    ev(
                        "margin_alert",
                        side=trade.side,
                        code=trade.contract_code,
                        qty=trade.qty,
                        price=mark,
                        reason="margin",
                        pnl=round(unreal),
                        pnl_on_margin_pct=round(pct, 1),
                    )
                )
        await self._session.flush()
        if trade is not None and mark is None:
            # Đang có vị thế nhưng lần quét này không có giá HĐ → không ghi tick (tránh P&L = 0 giả)
            return events
        realized_total = await self._realized_total(user_id, book)
        self._session.add(
            DerivSimTick(
                user_id=user_id,
                at=now,
                strategy=book,
                contract_code=trade.contract_code if trade else None,
                side=trade.side if trade else None,
                price=mark,
                unrealized_pnl=round(unreal),
                realized_total=round(realized_total),
                signal=suggestion,
            )
        )
        return events

    async def _realized_total(self, user_id: str, strategy: str) -> float:
        res = await self._session.execute(
            select(DerivSimTrade.realized_pnl).where(
                DerivSimTrade.user_id == user_id,
                DerivSimTrade.strategy == strategy,
                DerivSimTrade.realized_pnl.is_not(None),
            )
        )
        return float(sum(v for v in res.scalars().all() if v is not None))

    async def _closed(self, user_id: str) -> list[dict[str, Any]]:
        res = await self._session.execute(
            select(DerivSimTrade)
            .where(DerivSimTrade.user_id == user_id, DerivSimTrade.exit_at.is_not(None))
            .order_by(DerivSimTrade.id.desc())
            .limit(1000)
        )
        return [_trade_dict(t) for t in res.scalars().all()]

    # ── Xem ─────────────────────────────────────────────────────────────────
    def _open_view(self, t: DerivSimTrade, ticks: list[DerivSimTick]) -> dict[str, Any]:
        last = next(
            (
                k
                for k in reversed(ticks)
                if k.strategy == t.strategy and k.contract_code == t.contract_code
            ),
            None,
        )
        mark = last.price if last else None
        unreal = (
            trade_pnl(t.side, t.qty, t.entry_price, mark, FEE_PER_SIDE)
            if mark is not None
            else None
        )
        margin = initial_margin(mark or t.entry_price, t.qty)
        return {
            **_trade_dict(t),
            "mark_price": mark,
            "unrealized_pnl": round(unreal) if unreal is not None else None,
            "initial_margin": round(margin),
            "pnl_on_margin_pct": round(unreal / margin * 100, 2)
            if unreal is not None and margin
            else None,
        }

    async def view(self, user_id: str, today: date, tick_limit: int = 600) -> dict[str, Any]:
        cfg = await self._config(user_id)
        mode = (cfg.mode if cfg else None) or "follow"
        ticks_res = await self._session.execute(
            select(DerivSimTick)
            .where(DerivSimTick.user_id == user_id)
            .order_by(DerivSimTick.at.desc(), DerivSimTick.id.desc())
            .limit(tick_limit * 2)
        )
        ticks = list(reversed(ticks_res.scalars().all()))
        closed = await self._closed(user_id)
        books: dict[str, Any] = {}
        for b in BOOKS:
            t_open = await self._open_trade(user_id, b)
            b_closed = [t for t in closed if t["strategy"] == b]
            books[b] = {
                "open": self._open_view(t_open, ticks) if t_open else None,
                "trades": b_closed[:500],
                "stats": stats(b_closed),
                "ticks": [
                    {
                        "at": k.at,
                        "price": k.price,
                        "side": k.side,
                        "equity": round((k.realized_total or 0) + (k.unrealized_pnl or 0)),
                        "signal": k.signal,
                    }
                    for k in ticks
                    if k.strategy == b
                ][-tick_limit:],
            }
        primary = choose_primary(mode, books["base"]["stats"], books["fade"]["stats"])
        return {
            "config": {
                "active": bool(cfg.active) if cfg else False,
                "series": cfg.series if cfg else "F1M",
                "qty": cfg.qty if cfg else 1,
                "stop_points": cfg.stop_points if cfg else 15.0,
                "target_points": cfg.target_points if cfg else 30.0,
                "alert_margin_pct": cfg.alert_margin_pct if cfg else None,
                "mode": mode,
                "stop_atr": cfg.stop_atr if cfg else None,
                "target_atr": cfg.target_atr if cfg else None,
                "avoid_round": bool(cfg.avoid_round) if cfg else True,
                "crowd_atr": cfg.crowd_atr if cfg else 0.8,
                "started_at": cfg.started_at if cfg else None,
                "last_run_at": cfg.last_run_at if cfg else None,
                "last_signal": cfg.last_signal if cfg else None,
                "reentry_blocked_today": bool(cfg and cfg.reentry_blocked_on == today),
                "fee_per_side": FEE_PER_SIDE,
            },
            "primary": primary,
            # Sổ chính ở cấp cao nhất để UI đọc trực tiếp
            "open": books[primary]["open"],
            "trades": books[primary]["trades"],
            "stats": books[primary]["stats"],
            "ticks": books[primary]["ticks"],
            "books": books,
        }
