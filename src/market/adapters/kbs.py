"""KBS (KB Securities) market data adapter — SECONDARY realtime source.

Endpoint: POST https://kbbuddywts.kbsec.com.vn/iis-server/investment/stock/iss
Auth: none. Payload: {"code": "HPG,VNM,FPT"}

Lý do chọn KBS làm nguồn dự phòng thứ hai (thay vị trí của VNDirect finfo):
    - Là bảng giá realtime của một CTCK (giống VCI), không phải dữ liệu EOD.
    - Giá trả về đơn vị VND (cùng đơn vị với VCI) → so sánh trực tiếp được.
    - Có sẵn khối lượng luỹ kế phiên (TT) và giá mở cửa (OP) — đủ để đối soát
      giá mở/đóng cửa với VCI khi chốt phiên (xem market.price_reconciliation).

Mã trường (rút gọn, theo bảng giá KBS):
    SB symbol · EX sàn · RE tham chiếu · CL trần · FL sàn
    CP giá khớp hiện tại (= giá đóng cửa sau phiên) · OP mở cửa · HI cao · LO thấp
    CH thay đổi · CHP % thay đổi · TT KL luỹ kế (CP) · TV GT luỹ kế (VND)
    t thời điểm cập nhật · TD ngày giao dịch (DD/MM/YYYY)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from src.market.quote_service import MarketDataAdapter, Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)

_URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment/stock/iss"
_DERIVATIVE_URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment/derivative/iss"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "x-lang": "vi",
}
_TIMEOUT = 10.0
_BULK_CHUNK_SIZE = 50
_MAX_CONCURRENT_CHUNKS = 3


def _num(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_time(item: dict[str, Any]) -> datetime:
    raw = item.get("t")
    if isinstance(raw, (int, float)) and raw > 0:
        # epoch ms hoặc s
        secs = raw / 1000 if raw > 10_000_000_000 else raw
        return datetime.fromtimestamp(secs, tz=UTC)
    if isinstance(raw, str) and raw.strip():
        txt = raw.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S:%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(txt[:26], fmt)
            except ValueError:
                continue
    td = item.get("TD")
    if isinstance(td, str):
        try:
            return datetime.strptime(td.strip(), "%d/%m/%Y")
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def parse_kbs_item(item: dict[str, Any]) -> Quote:
    """Chuyển một dòng bảng giá KBS → Quote (đơn vị VND, volume = số CP)."""
    ticker = str(item["SB"]).upper()
    ref = _num(item.get("RE"))
    if not ref:
        raise ValueError(f"KBS: thiếu giá tham chiếu cho {ticker}")
    volume = int(_num(item.get("TT")) or 0)
    matched = _num(item.get("CP"))
    price = matched if matched else ref  # 0/None = chưa khớp
    change = _num(item.get("CH"))
    if change is None:
        change = price - ref
    change_pct = _num(item.get("CHP"))
    if change_pct is None:
        change_pct = change / ref * 100
    open_ = _num(item.get("OP")) or (price if volume > 0 else ref)
    return Quote(
        ticker=ticker,
        price=price,
        change=change,
        change_pct=change_pct,
        volume=volume,
        value=_num(item.get("TV")) or 0.0,
        open=open_,
        high=_num(item.get("HI")) or price,
        low=_num(item.get("LO")) or price,
        ref_price=ref,
        ceiling=_num(item.get("CL")) or ref,
        floor=_num(item.get("FL")) or ref,
        timestamp=_parse_time(item),
    )


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("data", [])
    return [r for r in payload or [] if isinstance(r, dict)]


class KBSAdapter(MarketDataAdapter):
    """Realtime quotes từ bảng giá KB Securities."""

    def __init__(self, timeout: float = _TIMEOUT) -> None:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=timeout)
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_CHUNKS)

    async def fetch_quote(self, ticker: str) -> Quote:
        results = await self.fetch_bulk_quotes([ticker])
        if not results:
            raise ValueError(f"KBS returned no data for ticker '{ticker}'.")
        return results[0]

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        chunks = [
            tickers[i : i + _BULK_CHUNK_SIZE] for i in range(0, len(tickers), _BULK_CHUNK_SIZE)
        ]

        async def _one(chunk: list[str]) -> list[dict[str, Any]]:
            async with self._sem:
                return await self._fetch_board(chunk)

        quotes: list[Quote] = []
        for raw in await asyncio.gather(*[_one(c) for c in chunks]):
            for item in raw:
                try:
                    quotes.append(parse_kbs_item(item))
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("kbs.parse_error", symbol=item.get("SB", "?"), error=str(exc))
        return quotes

    async def _fetch_board(self, symbols: list[str]) -> list[dict[str, Any]]:
        try:
            response = await self._client.post(
                _URL, json={"code": ",".join(s.upper() for s in symbols)}
            )
            response.raise_for_status()
            return _rows(response.json())
        except httpx.HTTPStatusError as exc:
            logger.error("kbs.http_error", status=exc.response.status_code, symbols=symbols)
            raise
        except httpx.TimeoutException:
            logger.error("kbs.timeout", symbols=symbols)
            raise

    async def fetch_derivative_quotes(self, codes: list[str]) -> list[Quote]:
        """Bảng giá phái sinh (HĐTL VN30, mã KRX vd 41I1GB000). Giá theo ĐIỂM chỉ số."""
        if not codes:
            return []
        try:
            response = await self._client.post(
                _DERIVATIVE_URL, json={"code": ",".join(c.upper() for c in codes)}
            )
            response.raise_for_status()
            rows = _rows(response.json())
        except httpx.HTTPError as exc:
            logger.error("kbs.derivative_error", codes=codes, error=str(exc))
            raise
        quotes: list[Quote] = []
        for item in rows:
            try:
                quotes.append(parse_kbs_item(item))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("kbs.derivative_parse_error", symbol=item.get("SB"), error=str(exc))
        return quotes

    async def close(self) -> None:
        await self._client.aclose()
