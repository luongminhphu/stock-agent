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


@dataclass(frozen=True)
class PriceSnapshot:
    """Giá + biến động tối thiểu mà một scanner thesis cần."""

    price: float
    atr14: float | None = None
    # "live" | "stale" | "fallback" (từ TickerContext) hoặc "quote" (đường get_quote cũ)
    source_quality: str = "quote"

    def stop_distance_atr(self, stop_loss: float | None) -> float | None:
        """(price - stop_loss) / ATR14 — dương = còn cách stop, âm = đã xuyên.

        None khi thiếu stop hoặc ATR (không có OHLCV / data fallback).
        """
        if stop_loss is None or not self.atr14 or self.atr14 <= 0:
            return None
        return (self.price - stop_loss) / self.atr14


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
                out[sym] = PriceSnapshot(
                    price=ctx.price,
                    atr14=ctx.atr14,
                    source_quality=str(ctx.source_quality),
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
