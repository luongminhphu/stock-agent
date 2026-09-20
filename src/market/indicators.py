"""Pure technical-indicator primitives — single source of truth for segment `market`.

Owner: market. Không I/O, không logging, không import ngoài stdlib → dùng được ở mọi
segment tiêu thụ market data (trend_engine, rrg_service, watchlist scan, thesis
stop-breach, ...) mà không kéo theo adapter.

Quy ước:
- Input là list[float] theo thứ tự thời gian tăng dần (cũ → mới).
- Hàm trả series giữ nguyên độ dài input; hàm trả scalar dùng giá trị mới nhất.
- Không đủ dữ liệu → trả giá trị trung tính đã document (không raise), để consumer
  quyết định hạ `source_quality` thay vì crash.

Lịch sử: `_ema` từng tồn tại 2 bản (trend_engine seed = phần tử đầu; rrg_service seed =
SMA của `period` phần tử đầu, tương đương pandas `ewm(span, adjust=False)` bootstrap).
Cả hai được giữ qua tham số `seed` để kết quả số học không đổi.
"""

from __future__ import annotations

from typing import Literal

EmaSeed = Literal["first", "sma"]

RSI_NEUTRAL = 50.0


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average của `period` giá trị cuối. None nếu thiếu dữ liệu."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int, *, seed: EmaSeed = "first") -> list[float]:
    """Exponential Moving Average, trả series cùng độ dài input.

    seed="first": out[0] = values[0]                  (trend_engine legacy)
    seed="sma":   out[0] = mean(values[:period])      (rrg_service legacy, pandas-like)
    """
    if not values:
        return []
    k = 2.0 / (period + 1)
    if seed == "sma":
        n = min(period, len(values))
        first = sum(values[:n]) / n
    else:
        first = values[0]
    out = [first]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int = 14) -> float:
    """RSI theo Wilder RMA (chuẩn TradingView/VCI/SSI).

    Cần tối thiểu period*2 + 1 bar; thiếu dữ liệu → RSI_NEUTRAL (50.0).
    """
    min_bars = period * 2 + 1
    if len(closes) < min_bars:
        return RSI_NEUTRAL

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, lo in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + lo) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1 + rs)), 2)


def macd_histogram(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """MACD histogram = MACD line − signal line. Thiếu dữ liệu (< slow + signal) → 0.0."""
    if len(closes) < slow + signal:
        return 0.0
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow, strict=True)]
    signal_line = ema(macd_line, signal)
    return macd_line[-1] - signal_line[-1]


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range (trung bình đơn giản của `period` TR cuối). Thiếu dữ liệu → 0.0."""
    if len(closes) < period + 1:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def volume_ratio(volumes: list[float], baseline: int = 20) -> float | None:
    """Khối lượng phiên mới nhất / trung bình `baseline` phiên trước đó.

    Dùng cho alert "volume spike" ở watchlist. None nếu thiếu dữ liệu hoặc baseline = 0.
    """
    if len(volumes) < baseline + 1:
        return None
    base = sum(volumes[-baseline - 1 : -1]) / baseline
    if base <= 0:
        return None
    return volumes[-1] / base


def high_low(values: list[float], lookback: int) -> tuple[float, float] | None:
    """(max, min) của `lookback` giá trị cuối — dùng cho đỉnh/đáy 52 tuần. None nếu rỗng."""
    if not values or lookback <= 0:
        return None
    window = values[-lookback:]
    return max(window), min(window)


__all__ = [
    "RSI_NEUTRAL",
    "EmaSeed",
    "atr",
    "ema",
    "high_low",
    "macd_histogram",
    "rsi",
    "sma",
    "volume_ratio",
]
