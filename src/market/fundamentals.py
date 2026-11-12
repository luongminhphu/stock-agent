"""Dữ liệu cơ bản point-in-time (P/E, tăng trưởng EPS) cho base rate — nguồn VCI.

Owner: market segment.

Nguồn: VCI IQ ``/v1/company/{mã}/statistics-financial`` — cùng endpoint
``vnstock.Finance(source="VCI").ratio()`` dùng. Mỗi dòng là một kỳ (năm, quý) với
P/E TTM, vốn hoá và số cổ phiếu lưu hành tại thời điểm cuối kỳ.

Quy đổi:
  EPS TTM  = vốn hoá ÷ số cổ phiếu ÷ P/E   (= giá cuối kỳ ÷ P/E)
  Tăng trưởng EPS = EPS TTM quý này ÷ EPS TTM cùng quý năm trước − 1 (chỉ khi cả hai > 0)

Point-in-time (không nhìn trước): số liệu của quý chỉ có hiệu lực từ ngày công bố ước
tính = cuối quý + 45 ngày (hạn nộp BCTC quý hợp nhất). Trước ngày đó, base rate vẫn
dùng số liệu của quý trước.

Lỗi mạng / dữ liệu → trả [] (base rate vẫn chạy với giá/khối lượng). Kết quả cache 12 giờ
vì số liệu chỉ đổi mỗi quý.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from src.market.baserate import FundamentalPoint
from src.platform.logging import get_logger

logger = get_logger(__name__)

_URL = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{symbol}/statistics-financial"
_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://trading.vietcap.com.vn",
    "Referer": "https://trading.vietcap.com.vn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
PUBLISH_LAG_DAYS = 45
_CACHE_TTL = 12 * 3600.0
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _period(row: dict[str, Any]) -> tuple[int, int] | None:
    y = row.get("year", row.get("yearReport"))
    q = row.get("quarter", row.get("lengthReport"))
    try:
        yi, qi = int(y), int(q)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return (yi, qi) if qi in _QUARTER_END and 2000 <= yi <= 2100 else None


def parse_statistics(rows: object) -> list[FundamentalPoint]:
    """Thuần: các dòng statistics-financial → FundamentalPoint theo ngày công bố ước tính.

    Bỏ dòng năm (quarter=5) và dòng thiếu kỳ. Trùng kỳ → giữ dòng sau cùng.
    """
    if not isinstance(rows, list):
        return []
    by_period: dict[tuple[int, int], dict[str, Any]] = {}
    for r in rows:
        if isinstance(r, dict) and (p := _period(r)) is not None:
            by_period[p] = r

    eps: dict[tuple[int, int], float | None] = {}
    for p, r in by_period.items():
        pe = _num(r.get("pe"))
        cap = _num(r.get("marketCap"))
        shares = _num(r.get("numberOfSharesMktCap"))
        eps[p] = cap / shares / pe if pe and pe > 0 and cap and shares and shares > 0 else None

    out: list[FundamentalPoint] = []
    for (y, q), r in sorted(by_period.items()):
        m, d = _QUARTER_END[q]
        published = date(y, m, d) + timedelta(days=PUBLISH_LAG_DAYS)
        pe = _num(r.get("pe"))
        cur, prev = eps.get((y, q)), eps.get((y - 1, q))
        growth = round(cur / prev - 1, 4) if cur and prev and cur > 0 and prev > 0 else None
        if pe is None and growth is None:
            continue
        out.append(FundamentalPoint(published_on=published, pe=pe, eps_growth_yoy=growth))
    return out


class VciFundamentals:
    """FundamentalsProvider cho BaseRateService — gọi VCI qua HostGuard, cache theo mã."""

    def __init__(self, ttl: float = _CACHE_TTL) -> None:
        self._ttl = ttl
        self._cache: dict[str, tuple[float, list[FundamentalPoint]]] = {}

    async def get_fundamentals(self, ticker: str) -> list[FundamentalPoint]:
        t = ticker.upper()
        hit = self._cache.get(t)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        points = await self._fetch(t)
        # Cache cả kết quả rỗng (mã không có số liệu / lỗi) để không gọi dồn nguồn.
        self._cache[t] = (time.monotonic(), points)
        return points

    async def _fetch(self, ticker: str) -> list[FundamentalPoint]:
        import httpx  # noqa: PLC0415

        from src.market.adapters.http_guard import guarded_request  # noqa: PLC0415

        try:
            async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
                resp = await guarded_request(client, "GET", _URL.format(symbol=ticker))
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.info("fundamentals.vci_unavailable", ticker=ticker, error=str(exc)[:200])
            return []
        data = body.get("data") if isinstance(body, dict) else body
        points = parse_statistics(data)
        logger.info("fundamentals.vci_ok", ticker=ticker, points=len(points))
        return points


_default: VciFundamentals | None = None


def default_fundamentals() -> VciFundamentals | None:
    """Nguồn mặc định cho BaseRateService. Tắt khi test hoặc MOCK_MARKET (không gọi mạng)."""
    global _default
    try:
        from src.platform.config import settings  # noqa: PLC0415

        if settings.mock_market or settings.environment == "test":
            return None
    except Exception:  # noqa: BLE001
        return None
    if _default is None:
        _default = VciFundamentals()
    return _default
