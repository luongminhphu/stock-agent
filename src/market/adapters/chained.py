"""ChainedAdapter — thử lần lượt các nguồn, nguồn sau chỉ dùng khi nguồn trước lỗi/thiếu.

Owner: market segment.

Design:
- fetch_quote: thử từng adapter theo thứ tự; adapter nào trả được thì dùng.
  Tất cả đều lỗi → raise lỗi cuối cùng.
- fetch_bulk_quotes: lấy trọn lô từ nguồn đầu tiên trả được; các mã nguồn đó
  KHÔNG trả về (mã mới, lỗi parse từng dòng...) được lấp bằng nguồn kế tiếp,
  thay vì âm thầm rơi khỏi kết quả như trước.
- Mỗi mã trong kết quả đến từ đúng một nguồn — không trộn trường giữa các nguồn,
  để open/close/high/low của một mã luôn nhất quán với nhau.
- close(): đóng mọi adapter; lỗi được log và nuốt.

Thứ tự mặc định (factory): VCI (Vietcap) → KBS (KB Securities) → VNDirect (EOD).

Chính sách VCI-first (factory truyền ``fallback_allowed``): trong giờ giao dịch CHỈ dùng
VCI — mọi dữ liệu thị trường đồng nhất một nguồn. Nguồn dự phòng chỉ được dùng ngoài giờ
giao dịch, cho những mã VCI không trả dữ liệu.
"""

from __future__ import annotations

from collections.abc import Callable

from src.market.quote_service import MarketDataAdapter, Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)


class ChainedAdapter(MarketDataAdapter):
    """Adapter nối chuỗi nhiều nhà cung cấp dữ liệu.

    Args:
        primary:   nguồn ưu tiên (VCIAdapter).
        secondary: nguồn dự phòng thứ nhất.
        *fallbacks: các nguồn dự phòng tiếp theo (tuỳ chọn).
        fallback_allowed: hàm trả True khi được dùng nguồn dự phòng (vd ngoài giờ giao
            dịch). None → luôn được dùng.
    """

    def __init__(
        self,
        primary: MarketDataAdapter,
        secondary: MarketDataAdapter,
        *fallbacks: MarketDataAdapter,
        fallback_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self._adapters: list[MarketDataAdapter] = [primary, secondary, *fallbacks]
        self._fallback_allowed = fallback_allowed

    def _active(self) -> list[MarketDataAdapter]:
        """Nguồn được dùng ở thời điểm hiện tại (chỉ nguồn chính khi chưa cho phép dự phòng)."""
        if self._fallback_allowed is None:
            return self._adapters
        try:
            allowed = self._fallback_allowed()
        except Exception:  # noqa: BLE001 — lỗi chính sách → an toàn: cho dùng dự phòng
            allowed = True
        return self._adapters if allowed else self._adapters[:1]

    @property
    def _primary(self) -> MarketDataAdapter:
        return self._adapters[0]

    @property
    def _secondary(self) -> MarketDataAdapter:
        return self._adapters[1]

    async def fetch_quote(self, ticker: str) -> Quote:
        last_exc: Exception | None = None
        for idx, adapter in enumerate(self._active()):
            try:
                quote = await adapter.fetch_quote(ticker)
                if idx:
                    logger.info("chained.quote.fallback_ok", ticker=ticker, source_index=idx)
                return quote
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "chained.quote.source_failed", ticker=ticker, source_index=idx, error=str(exc)
                )
        logger.error("chained.quote.all_failed", ticker=ticker)
        assert last_exc is not None
        raise last_exc

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        wanted = [t.upper() for t in tickers]
        found: dict[str, Quote] = {}
        last_exc: Exception | None = None
        any_ok = False
        for idx, adapter in enumerate(self._active()):
            missing = [t for t in wanted if t not in found]
            if not missing:
                break
            try:
                quotes = await adapter.fetch_bulk_quotes(missing)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "chained.bulk.source_failed",
                    source_index=idx,
                    count=len(missing),
                    error=str(exc),
                )
                continue
            any_ok = True
            for q in quotes:
                found.setdefault(q.ticker.upper(), q)
            if idx:
                logger.info("chained.bulk.fallback_used", source_index=idx, filled=len(quotes))
        if not any_ok:
            logger.error("chained.bulk.all_failed", count=len(wanted))
            assert last_exc is not None
            raise last_exc
        return [found[t] for t in wanted if t in found]

    async def close(self) -> None:
        """Close mọi adapter. Errors are logged and swallowed."""
        for idx, adapter in enumerate(self._adapters):
            try:
                await adapter.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("chained.close_failed", source_index=idx, error=str(exc))
