# src/market/adapters/tcbs_ohlcv.py
from datetime import date, datetime, time   # ✅ thêm datetime và time
import httpx
from src.market.ohlcv_service import OHLCVAdapter, Candle, Interval


class TCBSOHLCVAdapter(OHLCVAdapter):
    _BASE = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term"

    async def fetch_candles(
        self, ticker: str, from_date: date, to_date: date, interval: Interval = Interval.D1
    ) -> list[Candle]:
        resolution = "D"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self._BASE, params={
                "ticker": ticker,
                "type": "stock",
                "resolution": resolution,
                "from": int(datetime.combine(from_date, time.min).timestamp()),  # ✅ works
                "to":   int(datetime.combine(to_date, time.max).timestamp()),    # ✅ works
            })
            r.raise_for_status()
        data = r.json().get("data", [])
        return [
            Candle(
                ticker=ticker,
                date=date.fromtimestamp(d["tradingDate"] / 1000),
                open=d["open"], high=d["high"], low=d["low"], close=d["close"],
                volume=d["volume"], value=d.get("value", 0),
            )
            for d in data
        ]
