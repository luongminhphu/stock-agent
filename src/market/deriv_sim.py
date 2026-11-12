"""Giả lập giao dịch HĐTL VN30 (paper trading) — engine thuần, không I/O.

Owner: market segment.

Quy tắc (mỗi lần quét, mặc định 5 phút trong giờ giao dịch):
  1. Tín hiệu = base rate VN30 của phiên đã đóng cửa: Bullish → LONG, Bearish → SHORT,
     Neutral / không đủ dữ liệu → đứng ngoài. Không dự báo — chỉ theo thống kê tiền lệ,
     nên tín hiệu đổi tối đa một lần mỗi ngày.
  2. Đang có vị thế:
       - Stop-loss: lỗ ≥ stop_points điểm → đóng (reason=stop)
       - Take-profit: lãi ≥ target_points điểm → đóng (reason=target)
       - Sắp đáo hạn (≤ roll_days ngày) → đóng HĐ hiện tại, mở HĐ kỳ sau cùng chiều (roll)
       - Tín hiệu đổi chiều / về Neutral → đóng (signal), mở chiều mới nếu có
  3. Không có vị thế và có tín hiệu → mở theo kỳ hạn người dùng chọn. Sau khi bị Stop-loss /
     Take-profit thì chờ tới phiên sau mới vào lại (tránh vào-ra liên tục trong ngày).

Tư duy ngược (contrarian) — mọi cơ chế đều đo được bằng win rate, không mặc định là tốt hơn:
  - Shadow Fade: một sổ lệnh chạy song song đánh NGƯỢC tín hiệu. So win rate / P&L hai sổ
    cho biết tín hiệu đám đông (base rate công khai) đang có lợi thế hay đang bị đánh ngược.
    Chế độ ``auto`` chỉ chuyển sang theo sổ Fade khi số liệu đủ (≥ AUTO_MIN_TRADES lệnh mỗi
    sổ) và Fade vượt trội rõ (win rate ≥ +AUTO_EDGE và tổng P&L cao hơn).
  - Né số tròn: mức Stop-loss/Take-profit cách bội số ``round_step`` điểm trong vòng
    ``round_band`` → đẩy Stop ra XA hơn mức tròn (tránh vùng quét stop), kéo Target về GẦN hơn
    (khớp trước khi chạm vùng cản tâm lý). Có thể đặt khoảng cách theo ATR thay vì điểm cố định.
  - Không đuổi đám đông: sổ theo tín hiệu không mở mới khi VN30 trong phiên đã chạy cùng chiều
    tín hiệu ≥ ``crowd_atr`` × ATR (đã muộn, rủi ro mua đỉnh/bán đáy). Sổ Fade không bị lọc.

P&L tính theo hệ số 100.000 đ/điểm, trừ phí mỗi chiều (fee_per_side × số HĐ).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.market.derivatives import LONG, MULTIPLIER, SHORT

SERIES_ORDER = ("F1M", "F2M", "F1Q", "F2Q")
MODES = ("follow", "fade", "auto")
BOOKS = ("base", "fade")
AUTO_MIN_TRADES = 20
AUTO_EDGE = 0.10
SIGNAL_SIDE = {"NGHIENG_MUA": LONG, "NGHIENG_TRANH": SHORT}
DEFAULT_FEE_PER_SIDE = 5_000.0  # đồng / HĐ / chiều — phí sở + phí CTCK ước tính


@dataclass(frozen=True)
class SimParams:
    series: str = "F1M"
    qty: int = 1
    stop_points: float | None = 15.0
    target_points: float | None = 30.0
    roll_days: int = 2
    fee_per_side: float = DEFAULT_FEE_PER_SIDE
    stop_atr: float | None = None  # đặt thì khoảng Stop = stop_atr × ATR (ưu tiên hơn điểm)
    target_atr: float | None = None
    avoid_round: bool = True
    round_step: float = 5.0
    round_band: float = 1.0
    round_offset: float = 1.5
    crowd_atr: float | None = 0.8  # None → tắt lọc đám đông


@dataclass(frozen=True)
class SimPosition:
    code: str
    side: str
    qty: int
    entry_price: float
    stop_price: float | None = None
    target_price: float | None = None


@dataclass(frozen=True)
class SimAction:
    kind: str  # open | close
    code: str
    side: str
    qty: int
    price: float
    reason: str  # signal | stop | target | roll
    stop_price: float | None = None
    target_price: float | None = None


@dataclass
class StepResult:
    actions: list[SimAction] = field(default_factory=list)
    mark: float | None = None  # giá đánh dấu của vị thế còn mở sau bước này
    block_reentry: bool = False  # vừa Stop-loss/Take-profit → không vào lại trong phiên
    skipped: str | None = None  # "crowd" khi bỏ qua lệnh mở vì đám đông đã chạy trước


def signal_side(suggestion: str | None) -> str | None:
    return SIGNAL_SIDE.get(suggestion or "")


def _sign(side: str) -> int:
    return 1 if side == LONG else -1


def points(side: str, entry: float, price: float) -> float:
    return _sign(side) * (price - entry)


def trade_pnl(side: str, qty: int, entry: float, exit_: float, fee_per_side: float) -> float:
    """Lãi/lỗ đã trừ phí 2 chiều (đồng)."""
    return points(side, entry, exit_) * MULTIPLIER * qty - 2 * fee_per_side * qty


def _off_round(level: float, side: str, p: SimParams) -> float:
    """Đẩy mức Stop/Target khỏi số tròn. Với LONG cả hai đều dời xuống, SHORT dời lên:
    Stop ra xa entry hơn mức tròn, Target về gần entry hơn mức tròn."""
    if not p.avoid_round or p.round_step <= 0:
        return level
    r = round(level / p.round_step) * p.round_step
    if abs(level - r) > p.round_band:
        return level
    return round(r - _sign(side) * p.round_offset, 1)


def levels(
    side: str, entry: float, p: SimParams, atr: float | None = None
) -> tuple[float | None, float | None]:
    """Giá Stop-loss / Take-profit cho lệnh mới (cố định từ lúc vào lệnh)."""
    s = _sign(side)
    stop_d = p.stop_atr * atr if p.stop_atr and atr else p.stop_points
    tgt_d = p.target_atr * atr if p.target_atr and atr else p.target_points
    stop = _off_round(entry - s * abs(stop_d), side, p) if stop_d else None
    tgt = _off_round(entry + s * abs(tgt_d), side, p) if tgt_d else None
    return stop, tgt


def _pick_contract(
    contracts: Sequence[dict[str, Any]], series: str, roll_days: int
) -> dict[str, Any] | None:
    """HĐ theo kỳ hạn chọn; nếu đã sát đáo hạn thì lấy kỳ kế tiếp có giá."""
    by_series = {c.get("series"): c for c in contracts}
    start = SERIES_ORDER.index(series) if series in SERIES_ORDER else 0
    for s in SERIES_ORDER[start:]:
        c = by_series.get(s)
        if not c or c.get("price") is None:
            continue
        if (c.get("days_to_expiry") or 0) <= roll_days and s != SERIES_ORDER[-1]:
            continue
        return c
    return None


def step(
    position: SimPosition | None,
    suggestion: str | None,
    contracts: Sequence[dict[str, Any]],
    params: SimParams,
    reentry_blocked: bool = False,
    *,
    fade: bool = False,
    atr: float | None = None,
    move: float | None = None,
) -> StepResult:
    """Một lần quét: trả các lệnh giả lập cần thực hiện theo thứ tự.

    fade=True → sổ đánh ngược tín hiệu. atr / move (biến động VN30 trong phiên, điểm) dùng
    cho Stop/Target theo ATR và lọc đám đông.
    """
    res = StepResult()
    want = signal_side(suggestion)
    if fade and want is not None:
        want = SHORT if want == LONG else LONG
    by_code = {c.get("code"): c for c in contracts}

    def opener(code: str, side: str, qty: int, price: float, reason: str) -> SimAction:
        st, tg = levels(side, price, params, atr)
        return SimAction("open", code, side, qty, price, reason, st, tg)

    if position is not None:
        pos = position
        cur = by_code.get(pos.code)
        mark = cur.get("price") if cur else None
        if cur is None or mark is None:
            return res  # chưa có giá HĐ đang nắm → không làm gì, chờ lần quét sau
        stop_px, tgt_px = pos.stop_price, pos.target_price
        if stop_px is None and tgt_px is None:
            stop_px, tgt_px = levels(pos.side, pos.entry_price, params, atr)
        s = _sign(pos.side)

        def close(reason: str) -> None:
            res.actions.append(SimAction("close", pos.code, pos.side, pos.qty, mark, reason))

        if stop_px is not None and s * (mark - stop_px) <= 0:
            close("stop")
            res.block_reentry = True
            return res
        if tgt_px is not None and s * (mark - tgt_px) >= 0:
            close("target")
            res.block_reentry = True
            return res
        if (cur.get("days_to_expiry") or 0) <= params.roll_days:
            nxt = _pick_contract(contracts, params.series, params.roll_days)
            if nxt and nxt.get("code") != pos.code:
                close("roll")
                # Roll giữ chiều hiện tại; nếu tín hiệu đổi, lần quét sau sẽ đảo.
                res.actions.append(opener(nxt["code"], pos.side, pos.qty, nxt["price"], "roll"))
                res.mark = nxt["price"]
                return res
        if want != pos.side:
            close("signal")
        else:
            res.mark = mark
            return res

    if want is None or reentry_blocked:
        return res
    if (
        not fade
        and params.crowd_atr
        and atr
        and move is not None
        and _sign(want) * move >= params.crowd_atr * atr
    ):
        res.skipped = "crowd"
        return res
    c = _pick_contract(contracts, params.series, params.roll_days)
    if c is None:
        return res
    res.actions.append(opener(c["code"], want, params.qty, c["price"], "signal"))
    res.mark = c["price"]
    return res


def choose_primary(mode: str, base: dict[str, Any], fade: dict[str, Any]) -> str:
    """Sổ nào là sổ chính (gửi cảnh báo). auto: theo Fade chỉ khi đủ mẫu và vượt trội rõ."""
    if mode == "fade":
        return "fade"
    if mode != "auto":
        return "base"
    if min(base.get("trades", 0), fade.get("trades", 0)) < AUTO_MIN_TRADES:
        return "base"
    wb, wf = base.get("win_rate") or 0.0, fade.get("win_rate") or 0.0
    if wf >= wb + AUTO_EDGE and fade.get("total_pnl", 0) > base.get("total_pnl", 0):
        return "fade"
    return "base"


def atr(bars: Sequence[Any], n: int = 14) -> float | None:
    """Average True Range n phiên (nến có high/low/close)."""
    if len(bars) < n + 1:
        return None
    trs = []
    for prev, b in zip(bars[-n - 1 : -1], bars[-n:], strict=True):
        trs.append(max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close)))
    return round(sum(trs) / n, 2)


# ── Thống kê ────────────────────────────────────────────────────────────────


def stats(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Win rate & hiệu quả từ các lệnh giả lập đã đóng ({side, realized_pnl, ...}).

    Lệnh hoà (P&L = 0) không tính là thắng.
    """
    closed = [t for t in trades if t.get("realized_pnl") is not None]
    pnls = [float(t["realized_pnl"]) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    def _rate(xs: list[dict[str, Any]]) -> float | None:
        if not xs:
            return None
        return round(sum(1 for t in xs if float(t["realized_pnl"]) > 0) / len(xs), 4)

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    gross_loss = -sum(losses)
    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _rate(closed),
        "win_rate_long": _rate([t for t in closed if t.get("side") == LONG]),
        "win_rate_short": _rate([t for t in closed if t.get("side") == SHORT]),
        "avg_win": round(sum(wins) / len(wins)) if wins else None,
        "avg_loss": round(sum(losses) / len(losses)) if losses else None,
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "total_pnl": round(sum(pnls)),
        "max_drawdown": round(max_dd),
        "by_reason": {
            r: sum(1 for t in closed if t.get("exit_reason") == r)
            for r in ("signal", "stop", "target", "roll")
        },
    }


def is_trading_slot(now_ict_date: date, hhmm: int, is_trading_day: Any) -> bool:
    """Giờ giao dịch phái sinh: 08:45–11:30 và 13:00–14:45 (giờ VN), ngày giao dịch."""
    if not is_trading_day(now_ict_date):
        return False
    return 845 <= hhmm <= 1130 or 1300 <= hhmm <= 1445
