"""Wave C3 — một chỗ duy nhất để các scanner trong thesis lấy giá theo lô.

Trước C3, StopBreachService và DriftService mỗi cái tự loop ``get_quote`` từng mã
(N round-trip, không có ATR). Helper này:

- ưu tiên ``TickerContextService.get_many`` (1 bulk quote + OHLCV cache) → có ATR14
  và ``source_quality`` để downstream đo khoảng cách stop theo biến động;
- fallback ``get_quote`` từng mã cho những mã thiếu hoặc khi context service chưa wire.

Owner: thesis (đọc market, không chứa rule market). Không import segment khác
ngoài platform.logging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)

# ── Ngưỡng khoảng cách stop — một nơi duy nhất cho toàn thesis segment ────────
# Cảnh báo sớm khi giá còn cách stop dưới 1 ATR14 — chỉ quan sát, không invalidate.
# Dùng bởi StopBreachService (near_stop), WatchdogService, health_snapshot, readmodel.
NEAR_STOP_ATR = 1.0
# Fallback theo % khi không có OHLCV/ATR14 (đường get_quote cũ).
NEAR_STOP_PCT_FALLBACK = 5.0
CRITICAL_STOP_PCT_FALLBACK = 2.0

# Mức gần stop (thứ tự tăng dần độ khẩn): FAR < NEAR < CRITICAL < BREACHED
STOP_FAR = "FAR"
STOP_NEAR = "NEAR"
STOP_CRITICAL = "CRITICAL"  # chỉ xuất hiện ở đường fallback % (< 2%)
STOP_BREACHED = "BREACHED"


def stop_proximity(
    distance_pct: float | None,
    distance_atr: float | None,
) -> str | None:
    """Phân loại khoảng cách stop. None = không có dữ liệu (thiếu stop hoặc giá).

    - Có ATR14 (ưu tiên): <= 0 → BREACHED; < NEAR_STOP_ATR → NEAR; còn lại FAR.
    - Không có ATR14: dùng %: <= 0 → BREACHED; < 2% → CRITICAL; < 5% → NEAR; FAR.
    """
    if distance_atr is not None:
        if distance_atr <= 0:
            return STOP_BREACHED
        return STOP_NEAR if distance_atr < NEAR_STOP_ATR else STOP_FAR
    if distance_pct is not None:
        if distance_pct <= 0:
            return STOP_BREACHED
        if distance_pct < CRITICAL_STOP_PCT_FALLBACK:
            return STOP_CRITICAL
        if distance_pct < NEAR_STOP_PCT_FALLBACK:
            return STOP_NEAR
        return STOP_FAR
    return None


@dataclass(frozen=True)
class PriceSnapshot:
    """Giá + biến động tối thiểu mà một scanner thesis cần."""

    price: float
    atr14: float | None = None
    # "live" | "stale" | "fallback" (từ TickerContext) hoặc "quote" (đường get_quote cũ)
    source_quality: str = "quote"
    # Wave D2: dòng TickerContext.format_for_prompt() để scanner nhét vào prompt AI
    # mà không phải gọi context service lần hai. Rỗng khi đi đường get_quote.
    prompt_context: str = ""

    def stop_distance_atr(self, stop_loss: float | None) -> float | None:
        """(price - stop_loss) / ATR14 — dương = còn cách stop, âm = đã xuyên.

        None khi thiếu stop hoặc ATR (không có OHLCV / data fallback).
        """
        if stop_loss is None or not self.atr14 or self.atr14 <= 0:
            return None
        return (self.price - stop_loss) / self.atr14

    def stop_distance_pct(self, stop_loss: float | None) -> float | None:
        """(price - stop_loss) / price * 100 — dương = còn cách, âm = đã xuyên."""
        if stop_loss is None or self.price <= 0:
            return None
        return (self.price - stop_loss) / self.price * 100

    def stop_proximity(self, stop_loss: float | None) -> str | None:
        """FAR | NEAR | CRITICAL | BREACHED | None — xem ``stop_proximity()``."""
        return stop_proximity(self.stop_distance_pct(stop_loss), self.stop_distance_atr(stop_loss))


async def load_price_snapshots(
    tickers: list[str],
    *,
    ticker_context_service: Any | None,
    quote_service: Any | None,
    log_event: str = "thesis.price_snapshot",
) -> dict[str, PriceSnapshot]:
    """Trả map ticker → PriceSnapshot. Mã không lấy được giá sẽ vắng mặt."""
    syms = sorted({t.upper() for t in tickers if t})
    out: dict[str, PriceSnapshot] = {}
    if not syms:
        return out

    if ticker_context_service is not None:
        try:
            ctx_map = await ticker_context_service.get_many(syms)
            for sym, ctx in ctx_map.items():
                fmt = getattr(ctx, "format_for_prompt", None)
                out[sym] = PriceSnapshot(
                    price=ctx.price,
                    atr14=ctx.atr14,
                    source_quality=str(ctx.source_quality),
                    prompt_context=fmt() if callable(fmt) else "",
                )
        except Exception as exc:
            logger.warning(f"{log_event}.ticker_context_failed", count=len(syms), error=str(exc))

    missing = [s for s in syms if s not in out]
    if missing and quote_service is not None:
        for sym in missing:
            try:
                quote = await quote_service.get_quote(sym)
                out[sym] = PriceSnapshot(price=quote.price)
            except Exception as exc:
                logger.warning(f"{log_event}.quote_fetch_failed", ticker=sym, error=str(exc))

    return out
