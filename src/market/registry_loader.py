"""RegistryLoader — async data loader for SymbolRegistry dynamic engine.

Owner: market segment.

Responsibility:
    Populate SymbolRegistry._cache with SymbolInfo entries from two sources:

    Layer 1 — Static seed (_STATIC_SEED in registry.py)
        Always applied first as baseline (~60 curated tickers with key_metrics).
        Overridden by vnstock data when richer info is available.

    Layer 2 — vnstock Listing API (VCI source)
        Uses vnstock.Listing() to fetch:
          - symbols_by_exchange() → symbol, organ_name, exchange (3000+ tickers)
          - symbols_by_industries() → symbol, industry_code, industry_name (25 sectors)
        Merged by symbol → full name + exchange + sector coverage.
        Runs in asyncio.to_thread() since vnstock is sync-only.
        Fails gracefully if unavailable.

    Layer 3 — DB (Position, WatchlistItem, Thesis)
        Queries tickers present in user's data.
        Fills in metadata from static seed when available.
        For unknown tickers: registers with Sector.OTHER + name=ticker.

Sector mapping:
    vnstock industry_name strings (Vietnamese) → Sector enum via _VNSTOCK_SECTOR_MAP.
    Unknown industry strings → Sector.OTHER (never raises).

Usage:
    loader = RegistryLoader(session_factory=get_session)
    entries = await loader.load(static_seed)
    # entries: dict[str, SymbolInfo]
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from src.market.registry_types import Exchange, Sector, SymbolInfo
from src.platform.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# vnstock industry_name → Sector enum
# Source: vnstock.Listing().symbols_by_industries() — 25 industry_name values
# ---------------------------------------------------------------------------
_VNSTOCK_SECTOR_MAP: dict[str, Sector] = {
    # Banking / Finance
    "ngân hàng": Sector.BANKING,
    "bảo hiểm": Sector.FINANCIALS,
    "chứng khoán": Sector.FINANCIALS,
    "tài chính khác": Sector.FINANCIALS,
    # Real estate
    "bất động sản": Sector.REAL_ESTATE,
    # Technology
    "công nghệ và thông tin": Sector.TECHNOLOGY,
    # Materials
    "vật liệu xây dựng": Sector.MATERIALS,
    "sx nhựa - hóa chất": Sector.MATERIALS,
    "khai khoáng": Sector.MATERIALS,
    "sản phẩm cao su": Sector.MATERIALS,
    # Energy
    "tiện ích": Sector.ENERGY,  # Utilities → ENERGY (mostly power/gas in VN context)
    # Industrials
    "xây dựng": Sector.INDUSTRIALS,
    "vận tải - kho bãi": Sector.INDUSTRIALS,
    "sx phụ trợ": Sector.INDUSTRIALS,
    "sx thiết bị, máy móc": Sector.INDUSTRIALS,
    "thiết bị điện": Sector.INDUSTRIALS,
    "sx hàng gia dụng": Sector.INDUSTRIALS,
    # Consumer Goods
    "thực phẩm - đồ uống": Sector.CONSUMER_GOODS,
    "bán lẻ": Sector.CONSUMER_GOODS,
    "bán buôn": Sector.CONSUMER_GOODS,
    "chế biến thủy sản": Sector.CONSUMER_GOODS,
    "nông - lâm - ngư": Sector.CONSUMER_GOODS,
    "dịch vụ lưu trú, ăn uống, giải trí": Sector.CONSUMER_GOODS,
    # Healthcare
    "chăm sóc sức khỏe": Sector.HEALTHCARE,
    # Services / Other
    "dịch vụ tư vấn, hỗ trợ": Sector.OTHER,
}

# Exchange string from vnstock → Exchange enum
_EXCHANGE_MAP: dict[str, Exchange] = {
    "hose": Exchange.HOSE,
    "hnx": Exchange.HNX,
    "upcom": Exchange.UPCOM,
    "xhnf": Exchange.HNX,  # HNX derivatives/futures board
}


def _parse_sector(raw: str | None) -> Sector:
    """Map vnstock industry_name → Sector. Always returns a valid Sector."""
    if not raw:
        return Sector.OTHER
    key = raw.strip().lower()
    # Exact match first
    if key in _VNSTOCK_SECTOR_MAP:
        return _VNSTOCK_SECTOR_MAP[key]
    # Partial match — pick first hit
    for pattern, sector in _VNSTOCK_SECTOR_MAP.items():
        if pattern in key or key in pattern:
            return sector
    return Sector.OTHER


def _parse_exchange(raw: str | None) -> Exchange:
    if not raw:
        return Exchange.HOSE
    return _EXCHANGE_MAP.get(raw.strip().lower(), Exchange.HOSE)


# ---------------------------------------------------------------------------
# vnstock sync loader (runs in thread)
# ---------------------------------------------------------------------------

def _load_vnstock_sync() -> dict[str, SymbolInfo]:
    """Fetch full listing from vnstock (VCI source) — synchronous, no pandas.

    Called via asyncio.to_thread() to avoid blocking the event loop.
    Returns empty dict on any failure.
    """
    try:
        import vnstock

        listing = vnstock.Listing()

        # symbols_by_exchange → name + exchange for all 3000+ tickers
        # Returns a DataFrame-like object; we convert to records manually.
        exch_raw = listing.symbols_by_exchange()
        if exch_raw is None or len(exch_raw) == 0:
            return {}

        def _s(val: object) -> str:
            """Safe string coerce — handles pandas NaN (float) and None."""
            if val is None:
                return ""
            try:
                f = float(val)  # type: ignore[arg-type]
                if f != f:  # NaN check
                    return ""
            except (ValueError, TypeError):
                pass
            return str(val).strip()

        # Build exchange lookup: ticker → (organ_name, exchange_str)
        # Chỉ giữ type=='stock' — loại bỏ CW, ETF/fund, bond, corpbond, future.
        # vnstock trả về 3243 rows nhưng chỉ 1532 là stock thực sự.
        exch_map: dict[str, tuple[str, str]] = {}
        for row in exch_raw.itertuples(index=False):
            ticker = _s(getattr(row, "symbol", "")).upper()
            if not ticker:
                continue
            instrument_type = _s(getattr(row, "type", "stock")).lower()
            if instrument_type != "stock":
                continue  # skip CW (cw), ETF (fund), bond, corpbond, future
            name = _s(getattr(row, "organ_name", ""))
            exch_str = _s(getattr(row, "exchange", "")).upper()
            exch_map[ticker] = (name, exch_str)

        # symbols_by_industries → industry_name per ticker (~700 tickers)
        sector_map: dict[str, str] = {}  # ticker → industry_name
        try:
            indu_raw = listing.symbols_by_industries()
            if indu_raw is not None and len(indu_raw) > 0:
                for row in indu_raw.itertuples(index=False):
                    ticker = _s(getattr(row, "symbol", "")).upper()
                    industry = _s(getattr(row, "industry_name", ""))
                    if ticker:
                        sector_map[ticker] = industry
        except Exception:  # noqa: BLE001
            pass  # sector_map stays empty → Sector.OTHER for all

        # Merge: iterate exch_map, lookup sector_map
        entries: dict[str, SymbolInfo] = {}
        for ticker, (name, exch_str) in exch_map.items():
            exchange = _parse_exchange(exch_str)
            industry_name = sector_map.get(ticker, "")
            sector = _parse_sector(industry_name)
            entries[ticker] = SymbolInfo(
                ticker=ticker,
                name=name or ticker,
                exchange=exchange,
                sector=sector,
                key_metrics="",  # static seed has key_metrics; vnstock does not
            )

        return entries

    except Exception as exc:  # noqa: BLE001
        # Log detail but never propagate — caller handles empty dict gracefully
        logger.info(
            "registry_loader.vnstock_unavailable",
            exc_type=type(exc).__name__,
            detail=repr(exc),
            note="vnstock listing unavailable — using static seed + lazy VCI enrich",
        )
        return {}


# ---------------------------------------------------------------------------
# RegistryLoader
# ---------------------------------------------------------------------------

class RegistryLoader:
    """Load SymbolInfo entries from vnstock Listing API + DB tickers.

    Args:
        session_factory: Callable that returns an async SQLAlchemy session.
                         Pass None to skip DB enrichment (e.g. in tests).
    """

    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory

    async def load(
        self,
        static_seed: dict[str, SymbolInfo],
    ) -> dict[str, SymbolInfo]:
        """Return merged dict[ticker → SymbolInfo].

        Merge order (later overrides earlier):
          1. static_seed (baseline — always present, has key_metrics)
          2. vnstock listing (3000+ tickers with name/exchange/sector via VCI)
          3. DB tickers (ensures user-active tickers are always registered)

        Never raises — any layer that fails is skipped with an info log.
        """
        result: dict[str, SymbolInfo] = dict(static_seed)

        # Layer 2: vnstock listing (async-safe via thread)
        vnstock_entries = await asyncio.to_thread(_load_vnstock_sync)
        if vnstock_entries:
            # Merge: preserve key_metrics from static seed — only override
            # name/exchange/sector for tickers that had no sector info yet.
            for ticker, info in vnstock_entries.items():
                existing = result.get(ticker)
                if existing is None:
                    result[ticker] = info
                else:
                    # Upgrade sector if static seed left it as OTHER
                    # Upgrade name if static seed used ticker as placeholder
                    result[ticker] = SymbolInfo(
                        ticker=ticker,
                        name=info.name if (not existing.name or existing.name == ticker) else existing.name,
                        exchange=info.exchange if existing.exchange == Exchange.HOSE and info.exchange != Exchange.HOSE else existing.exchange,
                        sector=info.sector if existing.sector == Sector.OTHER and info.sector != Sector.OTHER else existing.sector,
                        key_metrics=existing.key_metrics,  # always preserve
                    )
            logger.info(
                "registry_loader.vnstock_ok",
                count=len(vnstock_entries),
                total=len(result),
            )
        else:
            logger.info(
                "registry_loader.vnstock_skipped",
                reason="vnstock unavailable — using static seed + lazy VCI enrich",
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

    async def _fetch_db_tickers(
        self,
        existing: dict[str, SymbolInfo],
    ) -> dict[str, SymbolInfo]:
        """Query DB for tickers in Position, WatchlistItem, Thesis.

        For tickers already in `existing`: no-op (preserve richer metadata).
        For new tickers: register with Sector.OTHER until vnstock/VCI enriches them.
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
                    name=ticker,           # placeholder — lazy VCI enrich will update
                    exchange=Exchange.HOSE,
                    sector=Sector.OTHER,
                    key_metrics="",
                )
        return new_entries


async def _query_db_tickers(session: Any) -> set[str]:
    """Collect all distinct tickers from Position, WatchlistItem, and Thesis tables."""
    from sqlalchemy import select

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
