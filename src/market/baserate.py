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
    "market",
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
    "market": {
        "bull": "Thị trường (VN-Index) trên MA200 và 3 tháng không giảm",
        "mixed": "Thị trường (VN-Index) giằng co",
        "bear": "Thị trường (VN-Index) dưới MA200 và 3 tháng giảm",
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
class PathStats:
    """Trong các lần tiền lệ: giá chạm Target hay Stop-loss trước (trong `horizon` phiên)."""

    stop_pct: float | None  # vd 0.12 = Stop-loss thấp hơn Entry 12%
    target_pct: float | None  # vd 0.08 = Target cao hơn Entry 8%
    horizon: int
    episodes: int
    target_first: float | None  # tỉ lệ 0..1
    stop_first: float | None
    neither: float | None  # hết kỳ chưa chạm mức nào
    median_days_to_target: float | None
    median_days_to_stop: float | None


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
    last_close: float | None = None  # giá đóng cửa phiên as_of — để đối chiếu với bảng giá CTCK
    path: PathStats | None = None  # xác suất chạm Target/Stop-loss trước trong các lần tiền lệ


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


def market_regimes(market_bars: Sequence[Bar]) -> list[tuple[date, str]]:
    """Trạng thái thị trường chung theo ngày (VN-Index): bull / mixed / bear.

    bull: trên MA200 và 3 tháng không giảm; bear: dưới MA200 và 3 tháng giảm; còn lại mixed.
    """
    closes = [float(b.close) for b in market_bars]
    ma200 = _rolling_mean(closes, 200)
    out: list[tuple[date, str]] = []
    for i, b in enumerate(market_bars):
        if ma200[i] is None or i < 63 or closes[i - 63] <= 0:
            continue
        above = closes[i] > (ma200[i] or 0)
        up = closes[i] / closes[i - 63] - 1 >= 0
        out.append((b.date, "bull" if above and up else "bear" if not (above or up) else "mixed"))
    return out


def compute_states(
    bars: Sequence[Bar],
    fundamentals: Sequence[FundamentalPoint] = (),
    market_bars: Sequence[Bar] = (),
) -> list[dict[str, str] | None]:
    """Trạng thái từng ngày (None khi chưa đủ dữ liệu warm-up). Chỉ dùng dữ liệu <= ngày đó."""
    n = len(bars)
    regimes = market_regimes(market_bars) if market_bars else []
    reg_idx = -1
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
        while reg_idx + 1 < len(regimes) and regimes[reg_idx + 1][0] <= d:
            reg_idx += 1
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
        # Thị trường chung tại cùng ngày (hoặc phiên gần nhất trước đó, tối đa 7 ngày)
        if reg_idx >= 0 and (d - regimes[reg_idx][0]).days <= 7:
            s["market"] = regimes[reg_idx][1]
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
    bars: Sequence[Bar],
    matches: list[int],
    n_eff: int,
    all_days: list[int],
    h: int,
    cost: float = ROUND_TRIP_COST,
) -> Outcome:
    """Phân phối lấy trên MỌI ngày khớp (đủ mọi vị trí trong một đợt trạng thái), nhưng
    độ tin cậy và mức co về nền dùng số đợt độc lập `n_eff` — không thổi phồng mẫu."""
    fwd = [r for r in (_forward(bars, i, h) for i in matches) if r is not None]
    base_fwd = [r for r in (_forward(bars, i, h) for i in all_days) if r is not None]
    base_wins = [r[0] > cost for r in base_fwd]
    base = sum(base_wins) / len(base_wins) if base_wins else 0.5
    rets = sorted(r[0] for r in fwd)
    rate = sum(1 for r in rets if r > cost) / len(rets) if rets else 0.0
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


def default_levels(main: Outcome) -> tuple[float | None, float | None]:
    """Stop/Target (tỉ lệ) cùng cách Kế hoạch nắm giữ gợi ý: Stop sâu hơn mức sụt điển hình
    20% (không có tiền lệ → 10%); Target = trung vị lợi nhuận (> 0) hoặc p90 (> 0)."""
    dd = main.avg_max_drawdown
    stop = abs(dd) * 1.2 if dd is not None and dd < 0 else 0.10
    tgt = None
    if main.median_return is not None and main.median_return > 0:
        tgt = main.median_return
    elif main.p90_return is not None and main.p90_return > 0:
        tgt = main.p90_return
    return round(stop, 4), round(tgt, 4) if tgt is not None else None


def path_stats(
    bars: Sequence[Bar],
    entries: Sequence[int],
    stop_pct: float | None,
    target_pct: float | None,
    horizon: int,
) -> PathStats:
    """Mỗi lần tiền lệ: mua ở giá đóng cửa ngày vào, theo dõi `horizon` phiên sau đó.
    Cùng phiên chạm cả hai mức → tính là chạm Stop-loss (thận trọng)."""
    t_days: list[int] = []
    s_days: list[int] = []
    n = 0
    for i in entries:
        if i + horizon >= len(bars) or bars[i].close <= 0:
            continue
        n += 1
        entry = float(bars[i].close)
        stop_px = entry * (1 - stop_pct) if stop_pct else None
        tgt_px = entry * (1 + target_pct) if target_pct else None
        for k in range(1, horizon + 1):
            b = bars[i + k]
            lo = float(getattr(b, "low", b.close))
            hi = float(getattr(b, "high", b.close))
            if stop_px is not None and lo <= stop_px:
                s_days.append(k)
                break
            if tgt_px is not None and hi >= tgt_px:
                t_days.append(k)
                break
    if n == 0:
        return PathStats(stop_pct, target_pct, horizon, 0, None, None, None, None, None)
    return PathStats(
        stop_pct=stop_pct,
        target_pct=target_pct,
        horizon=horizon,
        episodes=n,
        target_first=round(len(t_days) / n, 4),
        stop_first=round(len(s_days) / n, 4),
        neither=round((n - len(t_days) - len(s_days)) / n, 4),
        median_days_to_target=float(median(t_days)) if t_days else None,
        median_days_to_stop=float(median(s_days)) if s_days else None,
    )


def _confidence(n: int) -> str:
    if n < MIN_EPISODES:
        return "khong_du"
    if n < 10:
        return "thap"
    if n < 20:
        return "trung_binh"
    return "cao"


def _suggest(main: Outcome, cost: float = ROUND_TRIP_COST) -> str:
    if main.episodes < MIN_EPISODES:
        return SUGGEST_INSUFFICIENT
    med = main.median_return or 0.0
    if main.edge >= EDGE_THRESHOLD and med > cost:
        return SUGGEST_LEAN_BUY
    if main.edge <= -EDGE_THRESHOLD:
        return SUGGEST_LEAN_AVOID
    return SUGGEST_NEUTRAL


def _path_for(
    bars: Sequence[Bar],
    episodes: list[int],
    main: Outcome,
    horizon: int,
    stop_pct: float | None,
    target_pct: float | None,
) -> PathStats | None:
    if main.episodes < MIN_EPISODES:
        return None
    d_stop, d_tgt = default_levels(main)
    return path_stats(
        bars,
        episodes,
        stop_pct if stop_pct is not None else d_stop,
        target_pct if target_pct is not None else d_tgt,
        horizon,
    )


# ── API chính ────────────────────────────────────────────────────────────────


def analyze(
    ticker: str,
    bars: Sequence[Bar],
    fundamentals: Sequence[FundamentalPoint] = (),
    *,
    market_bars: Sequence[Bar] = (),
    horizons: tuple[int, ...] = HORIZONS,
    max_analogs: int = 30,
    main_horizon: int | None = None,
    cost: float = ROUND_TRIP_COST,
    stop_pct: float | None = None,
    target_pct: float | None = None,
) -> BaseRateReport:
    """Thống kê tiền lệ cho trạng thái của phiên cuối cùng trong `bars` (oldest-first).

    main_horizon: tầm nhìn quyết định gợi ý (mặc định tầm dài nhất — hợp nắm giữ cổ
        phiếu). HĐ tương lai nắm ngắn hơn nên truyền tầm ngắn hơn (vd 20 phiên).
    cost: chi phí khứ hồi (tỉ lệ) — lợi nhuận phải vượt mức này mới tính là thắng.
    stop_pct / target_pct: mức Stop-loss / Target (tỉ lệ) để thống kê chạm mức nào trước;
        None → mặc định như Kế hoạch nắm giữ gợi ý (default_levels).
    """
    ticker = ticker.upper()
    notes: list[str] = []
    states = compute_states(bars, fundamentals, market_bars)
    as_of = bars[-1].date if bars else date.today()
    current = states[-1] if states else None
    main_h = main_horizon if main_horizon in horizons else max(horizons)

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
            last_close=float(bars[-1].close) if bars else None,
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
        outcomes[h] = _outcome(bars, usable, len(_pick_episodes(usable, h)), all_days, h, cost)
    main = outcomes[main_h]
    analogs = []
    for i in episodes[-max_analogs:] if max_analogs > 0 else []:
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
        suggestion=_suggest(main, cost),
        path=_path_for(bars, episodes, main, main_h, stop_pct, target_pct),
        confidence=_confidence(main.episodes),
        history_from=bars[0].date,
        notes=notes,
        last_close=float(bars[-1].close),
    )


# ── Kiểm chứng độ tin (walk-forward) ────────────────────────────────────────

RELIABLE = "dang_tin"  # tín hiệu từng vượt mức nền rõ rệt
WEAK = "yeu"  # có tín hiệu nhưng không tốt hơn mức nền
UNPROVEN = "chua_du"  # quá ít lần có tín hiệu để kết luận
MIN_SIGNALS = 8


@dataclass(frozen=True)
class SignalRecord:
    suggestion: str
    count: int
    hit_rate: float | None  # Bullish: lãi > chi phí; Bearish: sau đó giảm; Neutral: None
    avg_return: float | None
    median_return: float | None


@dataclass(frozen=True)
class CalibrationBin:
    label: str  # vd "50–60%"
    count: int
    predicted: float  # xác suất app đưa ra (trung bình trong nhóm)
    realized: float  # tỉ lệ thắng thực tế


@dataclass(frozen=True)
class TrackRecord:
    ticker: str
    horizon: int
    step: int
    checked: int  # số lần kiểm (mỗi `step` phiên)
    period_from: date | None
    period_to: date | None
    base_win_rate: float | None  # mọi lần kiểm, không theo tín hiệu
    base_avg_return: float | None
    signals: dict[str, SignalRecord]
    calibration: list[CalibrationBin]
    verdict: str  # dang_tin | yeu | chua_du
    notes: list[str] = field(default_factory=list)


_CAL_EDGES = (0.0, 0.4, 0.5, 0.6, 0.7, 1.01)


def track_record(
    ticker: str,
    bars: Sequence[Bar],
    fundamentals: Sequence[FundamentalPoint] = (),
    *,
    market_bars: Sequence[Bar] = (),
    horizons: tuple[int, ...] = HORIZONS,
    main_horizon: int | None = None,
    cost: float = ROUND_TRIP_COST,
    step: int = 10,
) -> TrackRecord:
    """Chạy lại `analyze` tại các ngày trong quá khứ CHỈ với dữ liệu tới ngày đó (không nhìn
    trước), rồi so tín hiệu với kết quả thực tế sau `main_horizon` phiên."""
    main_h = main_horizon if main_horizon in horizons else max(horizons)
    start = WARMUP + main_h + MIN_EPISODES * main_h  # cần đủ lịch sử để có tiền lệ
    rows: list[tuple[str, float, float]] = []  # (gợi ý, xác suất thắng, lợi nhuận thực tế)
    first: date | None = None
    last: date | None = None
    for t in range(start, len(bars) - main_h, step):
        if bars[t].close <= 0:
            continue
        rep = analyze(
            ticker,
            bars[: t + 1],
            fundamentals,
            market_bars=market_bars,
            horizons=horizons,
            main_horizon=main_h,
            cost=cost,
            max_analogs=0,
        )
        main = rep.outcomes.get(main_h)
        if main is None:
            continue
        r = float(bars[t + main_h].close) / float(bars[t].close) - 1
        rows.append((rep.suggestion, main.win_prob, r))
        first = first or bars[t].date
        last = bars[t].date

    def rec(sug: str) -> SignalRecord:
        rs = [r for s_, _, r in rows if s_ == sug]
        hit: float | None = None
        if rs and sug == SUGGEST_LEAN_BUY:
            hit = round(sum(1 for r in rs if r > cost) / len(rs), 4)
        elif rs and sug == SUGGEST_LEAN_AVOID:
            hit = round(sum(1 for r in rs if r < 0) / len(rs), 4)
        return SignalRecord(
            suggestion=sug,
            count=len(rs),
            hit_rate=hit,
            avg_return=round(sum(rs) / len(rs), 4) if rs else None,
            median_return=round(median(rs), 4) if rs else None,
        )

    all_r = [r for _, _, r in rows]
    base_win = round(sum(1 for r in all_r if r > cost) / len(all_r), 4) if all_r else None
    signals = {
        s_: rec(s_)
        for s_ in (SUGGEST_LEAN_BUY, SUGGEST_LEAN_AVOID, SUGGEST_NEUTRAL, SUGGEST_INSUFFICIENT)
    }
    cal: list[CalibrationBin] = []
    for lo, hi in zip(_CAL_EDGES, _CAL_EDGES[1:], strict=False):
        grp = [(p, r) for s_, p, r in rows if s_ != SUGGEST_INSUFFICIENT and lo <= p < hi]
        if not grp:
            continue
        label = f"< {hi:.0%}" if lo == 0 else f"≥ {lo:.0%}" if hi > 1 else f"{lo:.0%}–{hi:.0%}"
        cal.append(
            CalibrationBin(
                label=label,
                count=len(grp),
                predicted=round(sum(p for p, _ in grp) / len(grp), 4),
                realized=round(sum(1 for _, r in grp if r > cost) / len(grp), 4),
            )
        )

    buy, avoid = signals[SUGGEST_LEAN_BUY], signals[SUGGEST_LEAN_AVOID]
    n_sig = buy.count + avoid.count
    notes = [
        f"Kiểm mỗi {step} phiên; kết quả {main_h} phiên của các lần kiểm liền nhau chồng lên "
        "nhau nên số lần độc lập ít hơn số lần kiểm.",
    ]
    if n_sig < MIN_SIGNALS or base_win is None:
        verdict = UNPROVEN
    else:
        good = 0
        if buy.count >= MIN_SIGNALS // 2 and buy.hit_rate is not None:
            good += 1 if buy.hit_rate >= base_win + 0.05 else -1
        if avoid.count >= MIN_SIGNALS // 2 and avoid.hit_rate is not None:
            good += 1 if avoid.hit_rate >= (1 - base_win) + 0.05 else -1
        verdict = RELIABLE if good > 0 else WEAK if good < 0 else UNPROVEN
    return TrackRecord(
        ticker=ticker.upper(),
        horizon=main_h,
        step=step,
        checked=len(rows),
        period_from=first,
        period_to=last,
        base_win_rate=base_win,
        base_avg_return=round(sum(all_r) / len(all_r), 4) if all_r else None,
        signals=signals,
        calibration=cal,
        verdict=verdict,
        notes=notes,
    )


# ── Service (I/O mỏng: lấy dữ liệu rồi gọi analyze) ─────────────────────────


class FundamentalsProvider(Protocol):
    async def get_fundamentals(self, ticker: str) -> list[FundamentalPoint]: ...


_AUTO = object()  # mặc định: nguồn dữ liệu cơ bản VCI (market.fundamentals), tắt khi test/mock


class BaseRateService:
    """Lấy ~10 năm nến ngày của mã + dữ liệu cơ bản (nếu có) rồi chạy `analyze`.

    Args:
        ohlcv_service: OHLCVService (get_candles).
        fundamentals:  nguồn P/E, tăng trưởng EPS theo ngày công bố; mặc định VCI
                       (market.fundamentals.default_fundamentals); None → bỏ qua.
        history_days:  số ngày lịch sử lấy về (mặc định ~10 năm).
    """

    def __init__(
        self,
        ohlcv_service: object,
        fundamentals: FundamentalsProvider | None | object = _AUTO,
        history_days: int = 3650,
    ) -> None:
        self._ohlcv = ohlcv_service
        if fundamentals is _AUTO:
            from src.market.fundamentals import default_fundamentals  # noqa: PLC0415

            fundamentals = default_fundamentals()
        self._fundamentals: FundamentalsProvider | None = fundamentals  # type: ignore[assignment]
        self._history_days = history_days

    async def analyze(self, ticker: str, as_of: date | None = None) -> BaseRateReport:
        """Tiền lệ cho trạng thái của PHIÊN ĐÃ ĐÓNG CỬA gần nhất.

        Trong giờ giao dịch, nến hôm nay còn chạy theo giá → trạng thái (MA, RSI, khối
        lượng) và gợi ý sẽ đổi liên tục trong phiên; với kỳ nắm giữ 3–6 tháng điều đó
        chỉ là nhiễu và làm bảng Danh mục lệch với drawer chi tiết. Nên bỏ nến của
        phiên chưa chốt giá (theo giờ đóng cửa từng sàn).

        Kết quả cache theo (mã, ngày + giá của nến cuối, số nến) — mọi nơi (bảng, drawer,
        kế hoạch) gọi trong cùng tiến trình nhận đúng một báo cáo.
        """
        t = ticker.upper()
        bars, funds, market = await self._load(t, as_of)
        key = self._key(t, bars, funds, market)
        cached = _REPORT_CACHE.get(key)
        if cached is not None:
            return cached
        rep = analyze(t, bars, funds, market_bars=market)
        if len(_REPORT_CACHE) >= _REPORT_CACHE_MAX:
            _REPORT_CACHE.clear()
        _REPORT_CACHE[key] = rep
        return rep

    async def track_record(self, ticker: str) -> TrackRecord:
        """Kiểm chứng độ tin (walk-forward) — tốn vài giây/mã nên chạy ở thread riêng và
        cache theo cùng khoá với báo cáo (đổi khi có nến mới)."""
        import asyncio  # noqa: PLC0415

        t = ticker.upper()
        bars, funds, market = await self._load(t, None)
        key = self._key(t, bars, funds, market)
        cached = _TRACK_CACHE.get(key)
        if cached is not None:
            return cached
        rec = await asyncio.to_thread(track_record, t, bars, funds, market_bars=market)
        if len(_TRACK_CACHE) >= _REPORT_CACHE_MAX:
            _TRACK_CACHE.clear()
        _TRACK_CACHE[key] = rec
        return rec

    async def _load(
        self, t: str, as_of: date | None
    ) -> tuple[list[Bar], list[FundamentalPoint], list[Bar]]:
        from datetime import timedelta  # noqa: PLC0415

        from src.market.ohlcv_service import _today_ict  # noqa: PLC0415

        today = _today_ict()
        end = as_of or today
        bars = await self._ohlcv.get_candles(  # type: ignore[attr-defined]
            t, end - timedelta(days=self._history_days), end
        )
        bars = list(bars or [])
        if as_of is None and bars and bars[-1].date >= today and not _session_closed(t):
            bars = bars[:-1]  # phiên hôm nay chưa chốt giá
        funds: list[FundamentalPoint] = []
        if self._fundamentals is not None:
            try:
                funds = await self._fundamentals.get_fundamentals(t)
            except Exception:  # noqa: BLE001 — thiếu dữ liệu cơ bản không chặn thống kê giá
                funds = []
        market = await self._market_bars(t, end, bars[-1].date if bars else None)
        return bars, funds, market

    @staticmethod
    def _key(
        t: str, bars: list[Bar], funds: list[FundamentalPoint], market: list[Bar]
    ) -> tuple[object, ...]:
        return (
            t,
            bars[-1].date if bars else None,
            round(float(bars[-1].close), 4) if bars else None,
            len(bars),
            len(funds),
            len(market),
        )

    async def _market_bars(self, ticker: str, end: date, last: date | None) -> list[Bar]:
        """Nến VN-Index cùng khoảng (bối cảnh thị trường). Chỉ số / lỗi → []."""
        from datetime import timedelta  # noqa: PLC0415

        if ticker in MARKET_INDEXES or last is None:
            return []
        try:
            rows = await self._ohlcv.get_candles(  # type: ignore[attr-defined]
                MARKET_SYMBOL, end - timedelta(days=self._history_days), end
            )
        except Exception:  # noqa: BLE001 — thiếu VN-Index không chặn thống kê của mã
            return []
        return [b for b in rows or [] if b.date <= last]


MARKET_SYMBOL = "VNINDEX"
MARKET_INDEXES = frozenset({"VNINDEX", "VN30", "HNXINDEX", "UPCOMINDEX", "VN30F1M"})

_REPORT_CACHE: dict[tuple[object, ...], BaseRateReport] = {}
_TRACK_CACHE: dict[tuple[object, ...], TrackRecord] = {}
_REPORT_CACHE_MAX = 500


def _session_closed(ticker: str) -> bool:
    """Phiên hôm nay của mã đã chốt giá đóng cửa chưa (HOSE/HNX 14:45, UPCoM 15:00)."""
    from src.market.price_rules import is_session_closed  # noqa: PLC0415
    from src.market.registry import registry  # noqa: PLC0415

    info = registry.get(ticker) if ticker not in MARKET_INDEXES else None
    exchange = info.exchange if info is not None else "HOSE"
    return is_session_closed(exchange)
