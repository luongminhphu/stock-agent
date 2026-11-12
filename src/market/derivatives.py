"""Hợp đồng tương lai chỉ số VN30 (VN30F) — lịch đáo hạn, lãi/lỗ, ký quỹ, beta, phòng hộ, basis.

Owner: market segment. Pure functions, không I/O.

Đặc tả HĐTL VN30 (HNX):
    - Tài sản cơ sở: chỉ số VN30. Hệ số nhân: 100.000 đ/điểm. Bước giá: 0,1 điểm.
    - 4 kỳ hạn lưu hành: tháng hiện tại (F1M), tháng kế tiếp (F2M), 2 tháng cuối quý
      gần nhất sau đó (F1Q, F2Q).
    - Ngày giao dịch cuối cùng / đáo hạn: thứ Năm tuần thứ 3 của tháng đáo hạn; trùng
      ngày nghỉ → ngày giao dịch liền trước.
    - Thanh toán lãi lỗ hằng ngày theo giá thanh toán cuối ngày (DSP); đáo hạn thanh toán
      bằng tiền theo giá trị chỉ số cơ sở (FSP).
    - Ký quỹ ban đầu = giá trị hợp đồng × tỉ lệ ký quỹ (VSDC công bố, thay đổi theo thời kỳ
      → tham số `im_rate`, mặc định IM_RATE_DEFAULT).

Phòng hộ danh mục cổ phiếu:
    beta_i   = cov(r_i, r_VN30) / var(r_VN30) trên `window` phiên lợi nhuận ngày
    beta_P   = Σ w_i · beta_i (w_i theo giá trị thị trường)
    N_short  = round(beta_P × Giá trị danh mục × tỉ lệ phòng hộ / (Giá HĐ × 100.000))
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median

MULTIPLIER = 100_000  # đ / điểm
TICK = 0.1  # điểm
IM_RATE_DEFAULT = 0.17  # tỉ lệ ký quỹ ban đầu tham khảo — cấu hình theo công bố VSDC
QUARTER_MONTHS = (3, 6, 9, 12)

LONG = "LONG"
SHORT = "SHORT"


# ── Lịch đáo hạn ─────────────────────────────────────────────────────────────


def _default_is_trading_day(d: date) -> bool:
    from src.market.trading_calendar import VNTradingCalendar  # noqa: PLC0415

    return VNTradingCalendar.is_trading_day(d)


def third_thursday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(days=14)


def expiry_date(
    year: int, month: int, is_trading_day: Callable[[date], bool] = _default_is_trading_day
) -> date:
    """Ngày giao dịch cuối cùng của HĐ đáo hạn tháng (year, month)."""
    d = third_thursday(year, month)
    for _ in range(10):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


def _add_month(y: int, m: int, k: int = 1) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + k
    return idx // 12, idx % 12 + 1


@dataclass(frozen=True)
class Contract:
    series: str  # F1M | F2M | F1Q | F2Q
    code: str  # VN30F2411 (YYMM)
    expiry: date
    krx: str = ""  # 41I1GB000 — mã giao dịch trên hệ thống KRX

    def days_to_expiry(self, today: date) -> int:
        return (self.expiry - today).days


def contract_code(year: int, month: int) -> str:
    return f"VN30F{year % 100:02d}{month:02d}"


_KRX_YEAR_ALPHABET = "ABCDEFGHJKLMNPQRSTVW"  # bỏ I, O, U


def krx_code(year: int, month: int) -> str:
    """Mã KRX (HNX áp dụng từ 05/2025): 41 + I1 (VN30) + mã năm + mã tháng + 000.

    Mã năm: chu kỳ 30 năm từ 2010 — 0–9 rồi A–W (bỏ I, O, U). Mã tháng: 1–9, A/B/C.
    VD: 11/2026 → 41I1GB000.
    """
    idx = (year - 2010) % 30
    y = str(idx) if idx <= 9 else _KRX_YEAR_ALPHABET[idx - 10]
    m = str(month) if month <= 9 else "ABC"[month - 10]
    return f"41I1{y}{m}000"


def active_contracts(
    today: date, is_trading_day: Callable[[date], bool] = _default_is_trading_day
) -> list[Contract]:
    """4 HĐ đang lưu hành tại ngày `today` (sau ngày đáo hạn tháng này → F1M là tháng sau)."""
    y, m = today.year, today.month
    if today > expiry_date(y, m, is_trading_day):
        y, m = _add_month(y, m)
    f1 = (y, m)
    f2 = _add_month(y, m)
    quarters: list[tuple[int, int]] = []
    qy, qm = f2
    while len(quarters) < 2:
        qy, qm = _add_month(qy, qm)
        if qm in QUARTER_MONTHS:
            quarters.append((qy, qm))
    out = []
    for series, (cy, cm) in zip(("F1M", "F2M", "F1Q", "F2Q"), (f1, f2, *quarters), strict=True):
        out.append(
            Contract(
                series,
                contract_code(cy, cm),
                expiry_date(cy, cm, is_trading_day),
                krx_code(cy, cm),
            )
        )
    return out


# ── Lãi/lỗ & ký quỹ ──────────────────────────────────────────────────────────


def _sign(side: str) -> int:
    if side.upper() not in (LONG, SHORT):
        raise ValueError(f"side phải là LONG/SHORT, nhận {side!r}")
    return 1 if side.upper() == LONG else -1


def pnl(side: str, qty: int, entry: float, price: float) -> float:
    """Lãi/lỗ (đồng) của `qty` hợp đồng từ giá vào `entry` tới `price`."""
    return _sign(side) * (price - entry) * MULTIPLIER * qty


def initial_margin(price: float, qty: int, im_rate: float = IM_RATE_DEFAULT) -> float:
    return price * MULTIPLIER * abs(qty) * im_rate


def round_tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 1)


@dataclass(frozen=True)
class DerivativePositionView:
    code: str
    side: str
    qty: int
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    pnl_points: float
    notional: float
    initial_margin: float
    pnl_on_margin_pct: float  # lãi/lỗ so với ký quỹ ban đầu — phản ánh đòn bẩy thật
    expiry: date | None
    days_to_expiry: int | None
    roll_warning: bool  # còn ≤ ROLL_WARN_DAYS ngày → nên cân nhắc chuyển kỳ hạn


ROLL_WARN_DAYS = 5


def position_view(
    code: str,
    side: str,
    qty: int,
    entry_price: float,
    mark_price: float,
    today: date,
    *,
    im_rate: float = IM_RATE_DEFAULT,
    is_trading_day: Callable[[date], bool] = _default_is_trading_day,
) -> DerivativePositionView:
    exp = expiry_for_code(code, is_trading_day)
    p = pnl(side, qty, entry_price, mark_price)
    im = initial_margin(entry_price, qty, im_rate)
    dte = (exp - today).days if exp else None
    return DerivativePositionView(
        code=code.upper(),
        side=side.upper(),
        qty=qty,
        entry_price=entry_price,
        mark_price=mark_price,
        unrealized_pnl=round(p),
        pnl_points=round(_sign(side) * (mark_price - entry_price), 1),
        notional=round(mark_price * MULTIPLIER * qty),
        initial_margin=round(im),
        pnl_on_margin_pct=round(p / im * 100, 2) if im else 0.0,
        expiry=exp,
        days_to_expiry=dte,
        roll_warning=dte is not None and 0 <= dte <= ROLL_WARN_DAYS,
    )


def _parse_code(code: str) -> tuple[int, int] | None:
    """VN30F2611 hoặc 41I1GB000 → (năm, tháng); mã chuỗi (VN30F1M…) → None."""
    c = code.upper().strip()
    if len(c) == 9 and c.startswith("VN30F") and c[5:].isdigit():
        yy, mm = int(c[5:7]), int(c[7:9])
        return (2000 + yy, mm) if 1 <= mm <= 12 else None
    if len(c) == 9 and c.startswith("41I1") and c.endswith("000"):
        yc, mc = c[4], c[5]
        idx = int(yc) if yc.isdigit() else _KRX_YEAR_ALPHABET.find(yc) + 10
        mm = int(mc) if mc.isdigit() else "ABC".find(mc) + 10
        if idx < 0 or not 1 <= mm <= 12:
            return None
        # chu kỳ 30 năm: chọn năm gần hiện tại nhất
        base = 2010 + idx
        return base + 30 * round((date.today().year - base) / 30), mm
    return None


def expiry_for_code(
    code: str, is_trading_day: Callable[[date], bool] = _default_is_trading_day
) -> date | None:
    """VN30F2411 / 41I1GB000 → ngày đáo hạn; mã chuỗi (VN30F1M…) → None."""
    ym = _parse_code(code)
    return expiry_date(ym[0], ym[1], is_trading_day) if ym else None


# ── Beta & phòng hộ ──────────────────────────────────────────────────────────


def daily_returns(closes: Sequence[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]


def beta(asset_returns: Sequence[float], index_returns: Sequence[float]) -> float | None:
    """Beta trên đoạn chung (căn theo phần cuối). < 20 điểm dữ liệu → None."""
    n = min(len(asset_returns), len(index_returns))
    if n < 20:
        return None
    a, b = list(asset_returns[-n:]), list(index_returns[-n:])
    ma, mb = mean(a), mean(b)
    var = sum((x - mb) ** 2 for x in b)
    if var == 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    return cov / var


@dataclass(frozen=True)
class HoldingBeta:
    ticker: str
    market_value: float
    weight: float
    beta: float | None


@dataclass(frozen=True)
class HedgePlan:
    portfolio_value: float
    portfolio_beta: float
    beta_exposure: float  # giá trị danh mục quy đổi theo beta (đ)
    hedge_ratio: float
    contract_code: str
    futures_price: float
    contracts_to_short: int
    hedged_value: float  # giá trị đã phòng hộ thực tế sau khi làm tròn số HĐ
    margin_required: float
    basis_points: float | None  # F - VN30: dương = HĐ đắt hơn cơ sở (Short hưởng lợi khi hội tụ)
    basis_cost: float | None  # chi phí/lợi ích từ basis nếu giữ tới đáo hạn (đ), + = có lợi
    holdings: list[HoldingBeta]
    missing_beta: list[str]  # mã không đủ dữ liệu → giả định beta = 1


def hedge_plan(
    holdings: Sequence[tuple[str, float, float | None]],
    futures_price: float,
    contract_code: str,
    *,
    hedge_ratio: float = 1.0,
    index_level: float | None = None,
    im_rate: float = IM_RATE_DEFAULT,
) -> HedgePlan:
    """holdings: (ticker, giá trị thị trường, beta | None)."""
    total = sum(v for _, v, _ in holdings)
    missing = [t for t, _, b in holdings if b is None]
    rows = [
        HoldingBeta(t, v, (v / total) if total else 0.0, b if b is None else round(b, 3))
        for t, v, b in holdings
    ]
    beta_p = sum(r.weight * (r.beta if r.beta is not None else 1.0) for r in rows)
    exposure = beta_p * total
    per_contract = futures_price * MULTIPLIER
    n = max(0, round(exposure * hedge_ratio / per_contract)) if per_contract > 0 else 0
    basis = round(futures_price - index_level, 1) if index_level else None
    return HedgePlan(
        portfolio_value=round(total),
        portfolio_beta=round(beta_p, 3),
        beta_exposure=round(exposure),
        hedge_ratio=hedge_ratio,
        contract_code=contract_code,
        futures_price=futures_price,
        contracts_to_short=n,
        hedged_value=round(n * per_contract / beta_p) if beta_p else 0,
        margin_required=round(initial_margin(futures_price, n, im_rate)),
        basis_points=basis,
        basis_cost=round(basis * MULTIPLIER * n) if basis is not None else None,
        holdings=rows,
        missing_beta=missing,
    )


# ── Basis ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BasisStats:
    current: float | None
    samples: int
    median: float | None
    p10: float | None
    p90: float | None
    percentile_of_current: float | None  # basis hiện tại cao hơn bao nhiêu % lịch sử
    by_days_to_expiry: dict[str, float]  # trung vị basis theo nhóm số ngày còn lại


def basis_stats(
    futures_close: dict[date, float],
    index_close: dict[date, float],
    expiries: Sequence[date],
    current: float | None = None,
) -> BasisStats:
    """Thống kê basis lịch sử (HĐ tháng gần nhất − VN30) theo ngày, nhóm theo ngày còn tới đáo hạn."""
    exp_sorted = sorted(expiries)
    rows: list[tuple[int, float]] = []
    for d, f in futures_close.items():
        idx = index_close.get(d)
        nxt = next((e for e in exp_sorted if e >= d), None)
        if idx is None or nxt is None:
            continue
        rows.append(((nxt - d).days, f - idx))
    vals = sorted(b for _, b in rows)
    if not vals:
        return BasisStats(current, 0, None, None, None, None, {})

    def q(p: float) -> float:
        pos = p * (len(vals) - 1)
        lo = math.floor(pos)
        hi = min(lo + 1, len(vals) - 1)
        return round(vals[lo] + (vals[hi] - vals[lo]) * (pos - lo), 2)

    buckets = {"0-3": (0, 3), "4-10": (4, 10), "11-20": (11, 20), ">20": (21, 10_000)}
    by_dte = {}
    for name, (lo, hi) in buckets.items():
        bs = [b for d, b in rows if lo <= d <= hi]
        if bs:
            by_dte[name] = round(median(bs), 2)
    pct = (
        round(sum(1 for v in vals if v < current) / len(vals) * 100, 1)
        if current is not None
        else None
    )
    return BasisStats(
        current=current,
        samples=len(vals),
        median=round(median(vals), 2),
        p10=q(0.10),
        p90=q(0.90),
        percentile_of_current=pct,
        by_days_to_expiry=by_dte,
    )


# ── Service (I/O mỏng) ───────────────────────────────────────────────────────


class DerivativesService:
    """Gom dữ liệu phái sinh cho API: giá HĐ đang lưu hành, VN30, basis, tiền lệ VN30, phòng hộ.

    Args:
        ohlcv_service:    OHLCVService — nến ngày của chỉ số "VN30" và chuỗi liên tục "VN30F1M".
        quote_source:     adapter có `fetch_derivative_quotes(krx_codes)` (KBSAdapter); None → không có giá HĐ.
    Tầm tiền lệ VN30 ngắn hơn cổ phiếu (5/20/60 phiên) vì HĐTL thường giữ ngắn.
    """

    VN30_SYMBOL = "VN30"
    CONTINUOUS_SYMBOL = "VN30F1M"
    BASERATE_HORIZONS = (5, 20, 60)

    def __init__(self, ohlcv_service: object, quote_source: object | None = None) -> None:
        self._ohlcv = ohlcv_service
        self._quotes = quote_source

    async def _candles(self, symbol: str, days: int) -> list:  # type: ignore[type-arg]
        from src.market.ohlcv_service import _today_ict  # noqa: PLC0415

        end = _today_ict()
        return await self._ohlcv.get_candles(symbol, end - timedelta(days=days), end)  # type: ignore[attr-defined]

    async def contracts(self, today: date) -> dict[str, object]:
        """HĐ đang lưu hành + giá (nếu có) + VN30 hiện tại + basis từng HĐ."""
        cons = active_contracts(today)
        prices: dict[str, float] = {}
        if self._quotes is not None:
            try:
                qs = await self._quotes.fetch_derivative_quotes([c.krx for c in cons])  # type: ignore[attr-defined]
                prices = {q.ticker.upper(): q.price for q in qs}
            except Exception:  # noqa: BLE001 — thiếu giá HĐ không chặn lịch đáo hạn
                prices = {}
        vn30 = await self._candles(self.VN30_SYMBOL, 10)
        level = vn30[-1].close if vn30 else None
        rows = []
        for c in cons:
            px = prices.get(c.krx) or prices.get(c.code)
            rows.append(
                {
                    "series": c.series,
                    "code": c.code,
                    "krx": c.krx,
                    "expiry": c.expiry,
                    "days_to_expiry": c.days_to_expiry(today),
                    "price": px,
                    "basis": round(px - level, 1) if px is not None and level else None,
                    "contract_value": round(px * MULTIPLIER) if px is not None else None,
                }
            )
        return {"as_of": today, "vn30": level, "contracts": rows}

    async def basis_history(self, today: date, days: int = 730) -> BasisStats:
        fut = await self._candles(self.CONTINUOUS_SYMBOL, days)
        idx = await self._candles(self.VN30_SYMBOL, days)
        start = today - timedelta(days=days)
        y, m = start.year, start.month
        stop = _add_month(today.year, today.month, 2)
        expiries = []
        while (y, m) <= stop:
            expiries.append(expiry_date(y, m))
            y, m = _add_month(y, m)
        fmap = {c.date: c.close for c in fut}
        imap = {c.date: c.close for c in idx}
        cur = None
        if fut and idx and fut[-1].date == idx[-1].date:
            cur = round(fut[-1].close - idx[-1].close, 1)
        return basis_stats(fmap, imap, expiries, current=cur)

    async def vn30_baserate(self) -> object:
        """Tiền lệ VN30 cho phiên ĐÃ ĐÓNG CỬA gần nhất (bỏ nến hôm nay nếu chưa chốt giá)."""
        from src.market.baserate import analyze  # noqa: PLC0415
        from src.market.ohlcv_service import _today_ict  # noqa: PLC0415
        from src.market.price_rules import is_session_closed  # noqa: PLC0415

        bars = list(await self._candles(self.VN30_SYMBOL, 3650))
        if bars and bars[-1].date >= _today_ict() and not is_session_closed("HOSE"):
            bars = bars[:-1]
        return analyze(self.VN30_SYMBOL, bars, horizons=self.BASERATE_HORIZONS)

    async def sim_context(self, today: date) -> dict[str, float | None]:
        """ATR14 VN30 (phiên đã đóng) + biến động VN30 trong phiên hôm nay (điểm) cho giả lập."""
        from src.market.deriv_sim import atr  # noqa: PLC0415

        bars = list(await self._candles(self.VN30_SYMBOL, 40))
        move = None
        if len(bars) >= 2 and bars[-1].date >= today:
            move = round(bars[-1].close - bars[-2].close, 2)
            bars = bars[:-1]
        return {"atr": atr(bars), "move": move}

    async def betas(self, tickers: list[str], window: int = 120) -> dict[str, float | None]:
        idx = await self._candles(self.VN30_SYMBOL, window * 2)
        imap = {c.date: c.close for c in idx}
        out: dict[str, float | None] = {}
        for t in tickers:
            try:
                bars = await self._candles(t, window * 2)
            except Exception:  # noqa: BLE001
                out[t] = None
                continue
            common = [c for c in bars if c.date in imap][-(window + 1) :]
            a = daily_returns([c.close for c in common])
            b = daily_returns([imap[c.date] for c in common])
            out[t] = beta(a, b)
        return out
