"""RegistryLoader — async data loader for SymbolRegistry dynamic engine.

Owner: market segment.

Responsibility:
    Populate SymbolRegistry._cache with SymbolInfo entries from two sources:

    Layer 1 — HTTP (VNDirect listing API)
        GET https://finfo-api.vndirect.com.vn/v4/stocks
        Fields: code, companyName, exchange, industryName
        Size: up to 2000 symbols (covers full HOSE + HNX + UPCoM)
        No auth required. Fails gracefully if unreachable.

    Layer 2 — DB (Position, WatchlistItem, Thesis)
        Queries tickers present in user's data.
        Fills in metadata from static seed when available.
        For unknown tickers: registers with Sector.OTHER + name=ticker.

    Layer 3 — Static seed (_STATIC_SEED in registry.py)
        Always applied first as baseline.
        Overridden by HTTP or DB data when richer info is available.

Sector mapping:
    VNDirect industryName strings → Sector enum via _SECTOR_MAP.
    Unknown industry strings → Sector.OTHER (never raises).

Usage:
    loader = RegistryLoader(session_factory=get_session)
    entries = await loader.load()
    # entries: dict[str, SymbolInfo]
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import httpx

from src.market.registry_types import Exchange, Sector, SymbolInfo
from src.platform.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# VNDirect finfo API config
# ---------------------------------------------------------------------------
_VNDIRECT_BASE    = "https://finfo-api.vndirect.com.vn/v4"
_VNDIRECT_FIELDS  = "code,companyName,exchange,industryName"
_VNDIRECT_SIZE    = 2000
_VNDIRECT_TIMEOUT = 15.0
_VNDIRECT_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.vndirect.com.vn",
    "Referer": "https://www.vndirect.com.vn/",
}

# ---------------------------------------------------------------------------
# VNDirect industryName → Sector enum mapping
# (covers HOSE/HNX/UPCoM standard industry classification strings)
# ---------------------------------------------------------------------------
_SECTOR_MAP: dict[str, Sector] = {
    # Banking / Finance
    "ngân hàng": Sector.BANKING,
    "bank": Sector.BANKING,
    "banking": Sector.BANKING,
    "tài chính": Sector.FINANCIALS,
    "financials": Sector.FINANCIALS,
    "chứng khoán": Sector.FINANCIALS,
    "securities": Sector.FINANCIALS,
    "bảo hiểm": Sector.FINANCIALS,
    "insurance": Sector.FINANCIALS,
    # Real estate
    "bất động sản": Sector.REAL_ESTATE,
    "real estate": Sector.REAL_ESTATE,
    "khu công nghiệp": Sector.REAL_ESTATE,
    # Technology
    "công nghệ thông tin": Sector.TECHNOLOGY,
    "technology": Sector.TECHNOLOGY,
    "information technology": Sector.TECHNOLOGY,
    # Materials
    "vật liệu": Sector.MATERIALS,
    "materials": Sector.MATERIALS,
    "hoá chất": Sector.MATERIALS,
    "chemicals": Sector.MATERIALS,
    "thép": Sector.MATERIALS,
    "steel": Sector.MATERIALS,
    "khoáng sản": Sector.MATERIALS,
    # Energy
    "năng lượng": Sector.ENERGY,
    "energy": Sector.ENERGY,
    "dầu khí": Sector.ENERGY,
    "oil & gas": Sector.ENERGY,
    "oil and gas": Sector.ENERGY,
    "điện": Sector.ENERGY,
    # Industrials
    "công nghiệp": Sector.INDUSTRIALS,
    "industrials": Sector.INDUSTRIALS,
    "xây dựng": Sector.INDUSTRIALS,
    "construction": Sector.INDUSTRIALS,
    "vận tải": Sector.INDUSTRIALS,
    "transportation": Sector.INDUSTRIALS,
    "logistics": Sector.INDUSTRIALS,
    "cảng biển": Sector.INDUSTRIALS,
    "hàng không": Sector.INDUSTRIALS,
    "dệt may": Sector.INDUSTRIALS,
    "textile": Sector.INDUSTRIALS,
    # Consumer
    "hàng tiêu dùng": Sector.CONSUMER_GOODS,
    "consumer": Sector.CONSUMER_GOODS,
    "consumer goods": Sector.CONSUMER_GOODS,
    "bán lẻ": Sector.CONSUMER_GOODS,
    "retail": Sector.CONSUMER_GOODS,
    "thực phẩm": Sector.CONSUMER_GOODS,
    "food": Sector.CONSUMER_GOODS,
    "đồ uống": Sector.CONSUMER_GOODS,
    "beverage": Sector.CONSUMER_GOODS,
    "thủy sản": Sector.CONSUMER_GOODS,
    "seafood": Sector.CONSUMER_GOODS,
    "nông nghiệp": Sector.CONSUMER_GOODS,
    "agriculture": Sector.CONSUMER_GOODS,
    # Healthcare
    "y tế": Sector.HEALTHCARE,
    "healthcare": Sector.HEALTHCARE,
    "dược phẩm": Sector.HEALTHCARE,
    "pharmaceuticals": Sector.HEALTHCARE,
    "pharma": Sector.HEALTHCARE,
    # Utilities
    "tiện ích": Sector.UTILITIES,
    "utilities": Sector.UTILITIES,
    "nước": Sector.UTILITIES,
    "water": Sector.UTILITIES,
    # Telecoms
    "viễn thông": Sector.TELECOMS,
    "telecoms": Sector.TELECOMS,
    "telecommunications": Sector.TELECOMS,
}

# Exchange string from VNDirect → Exchange enum
_EXCHANGE_MAP: dict[str, Exchange] = {
    "hose": Exchange.HOSE,
    "hnx": Exchange.HNX,
    "upcom": Exchange.UPCOM,
    "upcom": Exchange.UPCOM,
}


def _parse_sector(raw: str | None) -> Sector:
    """Map VNDirect industryName → Sector. Always returns a valid Sector."""
    if not raw:
        return Sector.OTHER
    key = raw.strip().lower()
    # Exact match first
    if key in _SECTOR_MAP:
        return _SECTOR_MAP[key]
    # Partial match — pick first hit
    for pattern, sector in _SECTOR_MAP.items():
        if pattern in key or key in pattern:
            return sector
    return Sector.OTHER


def _parse_exchange(raw: str | None) -> Exchange:
    if not raw:
        return Exchange.HOSE
    return _EXCHANGE_MAP.get(raw.strip().lower(), Exchange.HOSE)


# ---------------------------------------------------------------------------
# RegistryLoader
# ---------------------------------------------------------------------------

class RegistryLoader:
    """Load SymbolInfo entries from VNDirect API + DB tickers.

    Args:
        session_factory: Callable that returns an async SQLAlchemy session.
                         Pass None to skip DB enrichment (e.g. in tests).
        http_timeout:    Timeout in seconds for VNDirect HTTP call.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
        http_timeout: float = _VNDIRECT_TIMEOUT,
    ) -> None:
        self._session_factory = session_factory
        self._http_timeout = http_timeout

    async def load(
        self,
        static_seed: dict[str, SymbolInfo],
    ) -> dict[str, SymbolInfo]:
        """Return merged dict[ticker → SymbolInfo].

        Merge order (later overrides earlier):
          1. static_seed (baseline — always present)
          2. VNDirect listing API (richer name/sector for all markets)
          3. DB tickers (ensures user-active tickers are always registered)

        Never raises — any layer that fails is skipped with a warning log.
        """
        result: dict[str, SymbolInfo] = dict(static_seed)

        # Layer 2: HTTP
        http_entries = await self._fetch_vndirect()
        if http_entries:
            result.update(http_entries)
            logger.info(
                "registry_loader.http_ok",
                count=len(http_entries),
                total=len(result),
            )
        else:
            logger.info(
                "registry_loader.http_skipped",
                reason="VNDirect unavailable — using static seed + lazy VCI enrich",
            )

        # Layer 3: DB tickers (ensure coverage for user-active tickers)
        db_entries = await self._fetch_db_tickers(result)
        if db_entries:
            result.update(db_entries)
            logger.info(
                "registry_loader.db_ok",
                new_tickers=len(db_entries),
                total=len(result),
            )

        return result

    async def _fetch_vndirect(self) -> dict[str, SymbolInfo]:
        """Fetch full listing from VNDirect finfo API.

        Returns empty dict on any failure — caller handles gracefully.
        """
        try:
            async with httpx.AsyncClient(
                headers=_VNDIRECT_HEADERS,
                timeout=_VNDIRECT_TIMEOUT,
            ) as client:
                resp = await client.get(
                    f"{_VNDIRECT_BASE}/stocks",
                    params={
                        "q": "type:stock",
                        "fields": _VNDIRECT_FIELDS,
                        "size": _VNDIRECT_SIZE,
                    },
                )
                resp.raise_for_status()
                data: list[dict[str, Any]] = resp.json().get("data", [])
        except Exception as exc:  # noqa: BLE001
            # Expected on cloud deployments: VN broker WAFs block non-VN IPs.
            # Fallback to static seed is handled by caller — this is not an error.
            logger.info(
                "registry_loader.vndirect_unavailable",
                exc_type=type(exc).__name__,
                detail=repr(exc),
                note="cloud IP likely blocked by VNDirect WAF — static seed + VCI enrich will be used",
            )
            return {}

        entries: dict[str, SymbolInfo] = {}
        for item in data:
            ticker = (item.get("code") or "").strip().upper()
            if not ticker:
                continue
            name = (item.get("companyName") or ticker).strip()
            exchange = _parse_exchange(item.get("exchange"))
            sector = _parse_sector(item.get("industryName"))
            entries[ticker] = SymbolInfo(
                ticker=ticker,
                name=name,
                exchange=exchange,
                sector=sector,
                key_metrics="",   # HTTP source doesn't have key_metrics; static seed has it
            )
        return entries

    async def _fetch_db_tickers(
        self,
        existing: dict[str, SymbolInfo],
    ) -> dict[str, SymbolInfo]:
        """Query DB for tickers in Position, WatchlistItem, Thesis.

        For tickers already in `existing`: no-op (preserve richer metadata).
        For new tickers: register with Sector.OTHER until HTTP layer enriches them.
        Returns only the NEW entries to add.
        """
        if self._session_factory is None:
            return {}

        try:
            async with self._session_factory() as session:
                tickers = await _query_db_tickers(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("registry_loader.db_failed", error=str(exc))
            return {}

        new_entries: dict[str, SymbolInfo] = {}
        for ticker in tickers:
            if ticker not in existing:
                new_entries[ticker] = SymbolInfo(
                    ticker=ticker,
                    name=ticker,          # name unknown — use ticker as placeholder
                    exchange=Exchange.HOSE,
                    sector=Sector.OTHER,
                    key_metrics="",
                )
        return new_entries


async def _query_db_tickers(session: Any) -> set[str]:
    """Collect all distinct tickers from Position, WatchlistItem, and Thesis tables."""
    from sqlalchemy import select, union

    from src.portfolio.models import Position
    from src.watchlist.models import WatchlistItem

    tickers: set[str] = set()

    try:
        from src.thesis.models import Thesis
        has_thesis = True
    except ImportError:
        has_thesis = False

    try:
        # Position tickers
        pos_result = await session.execute(select(Position.ticker).distinct())
        tickers.update(r[0].upper() for r in pos_result.all() if r[0])

        # Watchlist tickers
        wl_result = await session.execute(select(WatchlistItem.ticker).distinct())
        tickers.update(r[0].upper() for r in wl_result.all() if r[0])

        # Thesis tickers
        if has_thesis:
            th_result = await session.execute(select(Thesis.ticker).distinct())
            tickers.update(r[0].upper() for r in th_result.all() if r[0])

    except Exception as exc:  # noqa: BLE001
        logger.warning("registry_loader.db_query_failed", error=str(exc))

    return tickers
