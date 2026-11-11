"""Base-rate engine — "Những lần trước mã này ở trạng thái như hôm nay, 3–6 tháng sau ra sao?"

Owner: market segment. Pure functions, không I/O, không AI.

Triết lý: không dự báo. Chỉ (1) mô tả trạng thái hiện tại bằng đặc trưng quan sát được,
(2) tìm các lần TRONG LỊCH SỬ CHÍNH MÃ ĐÓ có cùng trạng thái, (3) thống kê kết quả sau
60/120 phiên, (4) đối chiếu với mức nền của chính mã → gợi ý kèm xác suất và độ tin cậy.

Kỷ luật thống kê (đọc trước khi sửa):
  - Không nhìn trước: đặc trưng tại ngày t chỉ dùng dữ liệu <= t. Dữ liệu cơ bản dùng
    bản công bố gần nhất có published_on <= t (không dùng kỳ báo cáo chưa công bố).
  - Đợt độc lập: cửa sổ 120 phiên của các ngày liền nhau chồng lên nhau gần hết →
    phân phối kết quả lấy trên mọi ngày khớp, nhưng SỐ MẪU (độ tin cậy, mức co) chỉ
    tính theo ĐỢT không chồng nhau (cách nhau ≥ `horizon` phiên).
  - Mẫu nhỏ: một mã ~10 năm chỉ có vài chục đợt 120 phiên. Xác suất được co về mức nền
    của chính mã (Beta prior, PRIOR_WEIGHT quan sát giả) — n càng nhỏ càng gần mức nền.
  - So với mức nền, không so với 50%: mã tăng dài hạn thì "mua lúc nào cũng thắng".
  - "Thắng" = lợi nhuận vượt chi phí khứ hồi (phí 2 chiều + thuế bán), không phải > 0.
  - Nới điều kiện có kiểm soát: thiếu mẫu → bỏ dần đặc trưng kém quan trọng nhất và
    báo rõ đặc trưng nào đã dùng. Không đủ mẫu dù đã nới → không gợi ý.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from typing import Protocol

# ── Tham số ──────────────────────────────────────────────────────────────────

HORIZONS: tuple[int, ...] = (60, 120)  # ~3 và ~6 tháng giao dịch
ROUND_TRIP_COST = 0.004  # 0.15% phí × 2 chiều + 0.1% thuế bán
PRIOR_WEIGHT = 6.0  # số quan sát giả của prior (mức nền của chính mã)
MIN_EPISODES = 5  # dưới mức này: không gợi ý
EDGE_THRESHOLD = 0.10  # lệch ≥10 điểm % so với mức nền mới coi là có lợi thế
WARMUP = 252  # cần 1 năm dữ liệu để tính MA200 / vùng 52 tuần

# Thứ tự nới điều kiện: đặc trưng đứng CUỐI bị bỏ trước.
FEATURE_PRIORITY: tuple[str, ...] = (
    "trend",
    "range_52w",
    "valuation",
    "earnings",
    "momentum_3m",
    "volume",
)

FEATURE_LABELS: dict[str, dict[str, str]] = {
    "trend": {
        "above_both": "Giá trên MA50 và MA200",
        "mixed": "Giá nằm giữa MA50 và MA200",
        "below_both": "Giá dưới MA50 và MA200",
    },
    "range_52w": {
        "low": "Vùng thấp của biên độ 52 tuần",
        "mid": "Vùng giữa của biên độ 52 tuần",
        "high": "Vùng cao của biên độ 52 tuần",
    },
    "momentum_3m": {
        "down": "3 tháng qua giảm > 5%",
        "flat": "3 tháng qua đi ngang (±5%)",
        "up": "3 tháng qua tăng > 5%",
    },
    "volume": {
        "dry": "Khối lượng cạn (TB20 < 0.8× TB120)",
        "normal": "Khối lượng bình thường",
        "heavy": "Khối lượng lớn (TB20 > 1.5× TB120)",
    },
    "valuation": {
        "cheap": "P/E thuộc 1/3 thấp nhất lịch sử của mã",
        "fair": "P/E ở vùng giữa lịch sử của mã",
        "expensive": "P/E thuộc 1/3 cao nhất lịch sử của mã",
    },
    "earnings": {
        "declining": "EPS giảm so với cùng kỳ",
        "growing": "EPS tăng 0–20% so với cùng kỳ",
        "strong": "EPS tăng > 20% so với cùng kỳ",
    },
}

SUGGEST_LEAN_BUY = "NGHIENG_MUA"
SUGGEST_LEAN_AVOID = "NGHIENG_TRANH"
SUGGEST_NEUTRAL = "TRUNG_TINH"
SUGGEST_INSUFFICIENT = "KHONG_DU_TIEN_LE"


# ── Kiểu dữ liệu ─────────────────────────────────────────────────────────────


class Bar(Protocol):
    """Nến ngày (market.ohlcv_service.Candle thoả mãn). Giá nên là giá điều chỉnh."""

    date: date
    close: float
    low: float
    volume: int


@dataclass(frozen=True)
class FundamentalPoint:
    """Một bản số liệu cơ bản, có hiệu lực từ ngày công bố (point-in-time)."""

    published_on: date
    pe: float | None = None
    eps_growth_yoy: float | None = None  # 0.25 = +25% so với cùng kỳ


@dataclass(frozen=True)
class Outcome:
    horizon: int
    episodes: int
    win_prob: float  # đã co về mức nền
    raw_win_rate: float | None
    base_win_rate: float  # mức nền của chính mã
    edge: float  # win_prob - base_win_rate
    prob_low: float  # khoảng tin cậy ~80% của win_prob
    prob_high: float
    median_return: float | None
    p10_return: float | None
    p90_return: float | None
    base_median_return: float | None
    avg_max_drawdown: float | None  # trung bình mức sụt sâu nhất trong kỳ nắm giữ


@dataclass(frozen=True)
class Analog:
    """Một đợt tiền lệ — để người dùng tự kiểm tra từng lần."""

    entry_date: date
    entry_close: float
    returns: dict[int, float]
    max_drawdown: dict[int, float]


@dataclass(frozen=True)
class BaseRateReport:
    ticker: str
    as_of: date
    state: dict[str, str]  # toàn bộ đặc trưng hiện tại
    matched_on: tuple[str, ...]  # đặc trưng thực sự dùng để so khớp (sau khi nới)
    dropped: tuple[str, ...]  # đặc trưng đã bỏ do thiếu mẫu
    matching_days: int
    outcomes: dict[int, Outcome]
    analogs: list[Analog]
    suggestion: str
    confidence: str  # khong_du | thap | trung_binh | cao
    history_from: date | None
    notes: list[str] = field(default_factory=list)


# ── Đặc trưng ───────────────────────────────────────────────────────────────


def _rolling_mean(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        if i >= window - 1:
            out[i] = acc / window
    return out


def _bucket3(value: float, lo: float, hi: float, names: tuple[str, str, str]) -> str:
    if value < lo:
        return names[0]
    if value > hi:
        return names[2]
    return names[1]


def _fundamental_at(points: Sequence[FundamentalPoint], d: date) -> FundamentalPoint | None:
    """Bản công bố gần nhất có hiệu lực tại ngày d (points đã sort theo published_on)."""
    found: FundamentalPoint | None = None
    for p in points:
        if p.published_on > d:
            break
        found = p
    return found


def compute_states(
    bars: Sequence[Bar],
    fundamentals: Sequence[FundamentalPoint] = (),
) -> list[dict[str, str] | None]:
    """Trạng thái từng ngày (None khi chưa đủ dữ liệu warm-up). Chỉ dùng dữ liệu <= ngày đó."""
    n = len(bars)
    closes = [float(b.close) for b in bars]
    vols = [float(b.volume) for b in bars]
    ma50 = _rolling_mean(closes, 50)
    ma200 = _rolling_mean(closes, 200)
    v20 = _rolling_mean(vols, 20)
    v120 = _rolling_mean(vols, 120)
    funds = sorted(fundamentals, key=lambda p: p.published_on)
    pe_seen: list[float] = []  # P/E đã công bố tính tới ngày t — percentile point-in-time
    fund_idx = 0

    states: list[dict[str, str] | None] = []
    for i in range(n):
        d = bars[i].date
        while fund_idx < len(funds) and funds[fund_idx].published_on <= d:
            pe = funds[fund_idx].pe
            if pe is not None and pe > 0:
                pe_seen.append(pe)
            fund_idx += 1
        if i < WARMUP - 1 or ma50[i] is None or ma200[i] is None:
            states.append(None)
            continue
        c = closes[i]
        s: dict[str, str] = {}
        a50, a200 = c > (ma50[i] or 0), c > (ma200[i] or 0)
        s["trend"] = (
            "above_both" if a50 and a200 else "below_both" if not (a50 or a200) else "mixed"
        )
        window = closes[i - 251 : i + 1]
        lo, hi = min(window), max(window)
        pos = (c - lo) / (hi - lo) if hi > lo else 0.5
        s["range_52w"] = _bucket3(pos, 1 / 3, 2 / 3, ("low", "mid", "high"))
        ret3m = c / closes[i - 63] - 1 if closes[i - 63] > 0 else 0.0
        s["momentum_3m"] = _bucket3(ret3m, -0.05, 0.05, ("down", "flat", "up"))
        if v20[i] and v120[i]:
            s["volume"] = _bucket3(
                (v20[i] or 0) / (v120[i] or 1), 0.8, 1.5, ("dry", "normal", "heavy")
            )
        fp = _fundamental_at(funds, d) if funds else None
        if fp is not None:
            if fp.pe is not None and fp.pe > 0 and len(pe_seen) >= 6:
                rank = sum(1 for v in pe_seen if v < fp.pe) / len(pe_seen)
                s["valuation"] = _bucket3(rank, 1 / 3, 2 / 3, ("cheap", "fair", "expensive"))
            elif fp.pe is not None and fp.pe <= 0:
                s["valuation"] = "expensive"  # lỗ → P/E âm: coi như không rẻ
            if fp.eps_growth_yoy is not None:
                g = fp.eps_growth_yoy
                s["earnings"] = "declining" if g < 0 else "strong" if g > 0.20 else "growing"
        states.append(s)
    return states


# ── Kết quả & thống kê ───────────────────────────────────────────────────────


def _forward(bars: Sequence[Bar], i: int, h: int) -> tuple[float, float] | None:
    """(lợi nhuận, mức sụt sâu nhất) khi mua ở giá đóng cửa ngày i, giữ h phiên."""
    if i + h >= len(bars) or bars[i].close <= 0:
        return None
    entry = bars[i].close
    ret = bars[i + h].close / entry - 1
    lowest = min(b.low or b.close for b in bars[i + 1 : i + h + 1])
    return ret, min(0.0, lowest / entry - 1)


def _pick_episodes(days: list[int], gap: int) -> list[int]:
    """Chọn ngày bắt đầu đợt, mỗi đợt cách nhau ít nhất `gap` phiên (không chồng cửa sổ)."""
    picked: list[int] = []
    for i in days:
        if not picked or i - picked[-1] >= gap:
            picked.append(i)
    return picked


def _quantile(sorted_vals: list[float], q: float) -> float:
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _posterior(wins: int, n: int, base: float) -> tuple[float, float, float]:
    """Beta(base·k + wins, (1-base)·k + losses) → (mean, low, high) ~80% (xấp xỉ chuẩn)."""
    a = base * PRIOR_WEIGHT + wins
    b = (1 - base) * PRIOR_WEIGHT + (n - wins)
    mean = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
    return mean, max(0.0, mean - 1.2816 * sd), min(1.0, mean + 1.2816 * sd)


def _outcome(
    bars: Sequence[Bar], matches: list[int], n_eff: int, all_days: list[int], h: int
) -> Outcome:
    """Phân phối lấy trên MỌI ngày khớp (đủ mọi vị trí trong một đợt trạng thái), nhưng
    độ tin cậy và mức co về nền dùng số đợt độc lập `n_eff` — không thổi phồng mẫu."""
    fwd = [r for r in (_forward(bars, i, h) for i in matches) if r is not None]
    base_fwd = [r for r in (_forward(bars, i, h) for i in all_days) if r is not None]
    base_wins = [r[0] > ROUND_TRIP_COST for r in base_fwd]
    base = sum(base_wins) / len(base_wins) if base_wins else 0.5
    rets = sorted(r[0] for r in fwd)
    rate = sum(1 for r in rets if r > ROUND_TRIP_COST) / len(rets) if rets else 0.0
    n = n_eff if rets else 0
    mean, lo, hi = _posterior(round(rate * n), n, base)
    return Outcome(
        horizon=h,
        episodes=n,
        win_prob=round(mean, 4),
        raw_win_rate=round(rate, 4) if rets else None,
        base_win_rate=round(base, 4),
        edge=round(mean - base, 4),
        prob_low=round(lo, 4),
        prob_high=round(hi, 4),
        median_return=round(median(rets), 4) if rets else None,
        p10_return=round(_quantile(rets, 0.10), 4) if rets else None,
        p90_return=round(_quantile(rets, 0.90), 4) if rets else None,
        base_median_return=round(median(r[0] for r in base_fwd), 4) if base_fwd else None,
        avg_max_drawdown=round(sum(r[1] for r in fwd) / len(fwd), 4) if fwd else None,
    )


def _confidence(n: int) -> str:
    if n < MIN_EPISODES:
        return "khong_du"
    if n < 10:
        return "thap"
    if n < 20:
        return "trung_binh"
    return "cao"


def _suggest(main: Outcome) -> str:
    if main.episodes < MIN_EPISODES:
        return SUGGEST_INSUFFICIENT
    med = main.median_return or 0.0
    if main.edge >= EDGE_THRESHOLD and med > ROUND_TRIP_COST:
        return SUGGEST_LEAN_BUY
    if main.edge <= -EDGE_THRESHOLD:
        return SUGGEST_LEAN_AVOID
    return SUGGEST_NEUTRAL


# ── API chính ────────────────────────────────────────────────────────────────


def analyze(
    ticker: str,
    bars: Sequence[Bar],
    fundamentals: Sequence[FundamentalPoint] = (),
    *,
    horizons: tuple[int, ...] = HORIZONS,
    max_analogs: int = 30,
) -> BaseRateReport:
    """Thống kê tiền lệ cho trạng thái của phiên cuối cùng trong `bars` (oldest-first)."""
    ticker = ticker.upper()
    notes: list[str] = []
    states = compute_states(bars, fundamentals)
    as_of = bars[-1].date if bars else date.today()
    current = states[-1] if states else None
    main_h = max(horizons)

    if current is None:
        return BaseRateReport(
            ticker=ticker,
            as_of=as_of,
            state={},
            matched_on=(),
            dropped=(),
            matching_days=0,
            outcomes={},
            analogs=[],
            suggestion=SUGGEST_INSUFFICIENT,
            confidence="khong_du",
            history_from=bars[0].date if bars else None,
            notes=[f"Cần ít nhất {WARMUP} phiên lịch sử để mô tả trạng thái."],
        )
    if not fundamentals:
        notes.append("Chưa có dữ liệu cơ bản (P/E, EPS) — chỉ so khớp theo giá/khối lượng.")

    all_days = [i for i, s in enumerate(states) if s is not None]
    keys = [k for k in FEATURE_PRIORITY if k in current]
    dropped: list[str] = []
    # Ngày khớp: loại phiên cuối (chính nó) và chỉ lấy ngày còn đủ dữ liệu tương lai
    while True:
        matches = [
            i for i in all_days[:-1] if all((states[i] or {}).get(k) == current[k] for k in keys)
        ]
        episodes = _pick_episodes([i for i in matches if i + main_h < len(bars)], main_h)
        if len(episodes) >= MIN_EPISODES or len(keys) <= 1:
            break
        dropped.append(keys.pop())
    if dropped:
        notes.append(
            "Không đủ tiền lệ khớp toàn bộ trạng thái — đã bỏ điều kiện: " + ", ".join(dropped)
        )

    # Số đợt độc lập theo từng tầm nhìn (tầm ngắn có nhiều đợt hơn)
    outcomes = {}
    for h in horizons:
        usable = [i for i in matches if i + h < len(bars)]
        outcomes[h] = _outcome(bars, usable, len(_pick_episodes(usable, h)), all_days, h)
    main = outcomes[main_h]
    analogs = []
    for i in episodes[-max_analogs:]:
        rets: dict[int, float] = {}
        mdds: dict[int, float] = {}
        for h in horizons:
            f = _forward(bars, i, h)
            if f is not None:
                rets[h], mdds[h] = round(f[0], 4), round(f[1], 4)
        analogs.append(
            Analog(
                entry_date=bars[i].date,
                entry_close=bars[i].close,
                returns=rets,
                max_drawdown=mdds,
            )
        )

    return BaseRateReport(
        ticker=ticker,
        as_of=as_of,
        state=dict(current),
        matched_on=tuple(keys),
        dropped=tuple(dropped),
        matching_days=len(matches),
        outcomes=outcomes,
        analogs=analogs,
        suggestion=_suggest(main),
        confidence=_confidence(main.episodes),
        history_from=bars[0].date,
        notes=notes,
    )


# ── Service (I/O mỏng: lấy dữ liệu rồi gọi analyze) ─────────────────────────


class FundamentalsProvider(Protocol):
    async def get_fundamentals(self, ticker: str) -> list[FundamentalPoint]: ...


class BaseRateService:
    """Lấy ~10 năm nến ngày của mã + dữ liệu cơ bản (nếu có) rồi chạy `analyze`.

    Args:
        ohlcv_service: OHLCVService (get_candles).
        fundamentals:  nguồn P/E, tăng trưởng EPS theo ngày công bố; None → bỏ qua.
        history_days:  số ngày lịch sử lấy về (mặc định ~10 năm).
    """

    def __init__(
        self,
        ohlcv_service: object,
        fundamentals: FundamentalsProvider | None = None,
        history_days: int = 3650,
    ) -> None:
        self._ohlcv = ohlcv_service
        self._fundamentals = fundamentals
        self._history_days = history_days

    async def analyze(self, ticker: str, as_of: date | None = None) -> BaseRateReport:
        from datetime import timedelta  # noqa: PLC0415

        from src.market.ohlcv_service import _today_ict  # noqa: PLC0415

        end = as_of or _today_ict()
        bars = await self._ohlcv.get_candles(  # type: ignore[attr-defined]
            ticker.upper(), end - timedelta(days=self._history_days), end
        )
        funds: list[FundamentalPoint] = []
        if self._fundamentals is not None:
            try:
                funds = await self._fundamentals.get_fundamentals(ticker.upper())
            except Exception:  # noqa: BLE001 — thiếu dữ liệu cơ bản không chặn thống kê giá
                funds = []
        return analyze(ticker, bars, funds)
