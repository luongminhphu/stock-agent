"""SymbolRegistry — dynamic engine for Vietnamese equity symbol metadata.

Owner: market segment.

Architecture — 3-layer data pipeline:
    Layer 1 (static seed)  : ~100 curated tickers with key_metrics annotations.
                             Always applied first. Used as baseline + fallback.
    Layer 2 (HTTP)         : VNDirect finfo API listing — full HOSE/HNX/UPCoM
                             (~1500+ symbols, name + sector + exchange).
                             Applied on startup; refreshed every TTL_HOURS.
    Layer 3 (DB)           : Tickers from Position/Watchlist/Thesis in the DB.
                             Ensures user-active tickers are always registered
                             even if missing from HTTP source.

Runtime enrich:
    Any segment can call registry.enrich(ticker, ...) to register or update
    a ticker at runtime (e.g. when user adds a new watchlist/thesis entry).

Startup:
    await registry.initialize(session_factory=...) — called from bootstrap.
    Safe to call multiple times; subsequent calls are no-ops within TTL window.

Sync reads (after init):
    registry.resolve(ticker)         → SymbolInfo  (raises SymbolNotFoundError)
    registry.get(ticker)             → SymbolInfo | None
    registry.exists(ticker)          → bool
    registry.all_tickers()           → list[str]
    registry.list_by_sector(sector)  → list[SymbolInfo]
    registry.list_by_exchange(exch)  → list[SymbolInfo]
    registry.get_sector_map()        → dict[sector_name, list[ticker]]
    registry.get_sector_context_str(ticker) → str (for AI prompt injection)

Backward compatibility:
    Exchange, Sector, SymbolInfo re-exported here — all existing imports work.
    SymbolNotFoundError re-exported.
    Module-level `registry` singleton preserved.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from src.market.registry_types import Exchange, Sector, SymbolInfo
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Re-export for backward compatibility
__all__ = [
    "Exchange",
    "Sector",
    "SymbolInfo",
    "SymbolNotFoundError",
    "SymbolRegistry",
    "registry",
]

# Cache TTL — registry refreshes from HTTP+DB every 24 hours
_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Static seed — curated tickers with AI key_metrics annotations
# Always used as baseline; overridden by HTTP/DB if richer data is available.
# Adding a ticker here adds key_metrics which HTTP source cannot provide.
# ---------------------------------------------------------------------------
_STATIC_SEED: dict[str, SymbolInfo] = {
    # ── HOSE — VN30 (Q1/2026) ──────────────────────────────────────────
    "VCB": SymbolInfo("VCB", "Vietcombank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VHM": SymbolInfo("VHM", "Vinhomes", Exchange.HOSE, Sector.REAL_ESTATE,
        "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án, tồn kho"),
    "VIC": SymbolInfo("VIC", "Vingroup", Exchange.HOSE, Sector.REAL_ESTATE,
        "lãi suất vay mua nhà, tiến độ dự án VinFast, dòng tiền tập đoàn"),
    "FPT": SymbolInfo("FPT", "FPT Corporation", Exchange.HOSE, Sector.TECHNOLOGY,
        "tăng trưởng IT outsourcing, biên lợi nhuận mảng nước ngoài, tỷ giá"),
    "BID": SymbolInfo("BID", "BIDV", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "HPG": SymbolInfo("HPG", "Hoa Phat Group", Exchange.HOSE, Sector.MATERIALS,
        "giá thép HRC, giá quặng sắt, hoạt động xây dựng, xuất khẩu thép"),
    "GAS": SymbolInfo("GAS", "PetroVietnam Gas", Exchange.HOSE, Sector.ENERGY,
        "giá khí LNG, nhu cầu điện, hợp đồng Petro Vietnam"),
    "CTG": SymbolInfo("CTG", "VietinBank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "TCB": SymbolInfo("TCB", "Techcombank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "MBB": SymbolInfo("MBB", "MB Bank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "MSN": SymbolInfo("MSN", "Masan Group", Exchange.HOSE, Sector.CONSUMER_GOODS,
        "sức mua tiêu dùng, giá nguyên liệu đầu vào, tỷ giá"),
    "VRE": SymbolInfo("VRE", "Vincom Retail", Exchange.HOSE, Sector.REAL_ESTATE,
        "tỷ lệ lấp đầy mặt bằng, sức mua tiêu dùng, mở rộng trung tâm thương mại"),
    "SAB": SymbolInfo("SAB", "Sabeco", Exchange.HOSE, Sector.CONSUMER_GOODS,
        "sản lượng bia, sức mua tiêu dùng, thuế tiêu thụ đặc biệt"),
    "ACB": SymbolInfo("ACB", "Asia Commercial Bank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VNM": SymbolInfo("VNM", "Vinamilk", Exchange.HOSE, Sector.CONSUMER_GOODS,
        "giá sữa bột nhập khẩu, sức mua, thị phần nội địa"),
    "MWG": SymbolInfo("MWG", "The Gioi Di Dong", Exchange.HOSE, Sector.CONSUMER_GOODS,
        "sức mua tiêu dùng, tỷ lệ mở rộng cửa hàng, biên lợi nhuận"),
    "PLX": SymbolInfo("PLX", "Petrolimex", Exchange.HOSE, Sector.ENERGY,
        "giá dầu thô, biên lợi nhuận kinh doanh xăng dầu, tỷ giá"),
    "POW": SymbolInfo("POW", "PetroVietnam Power", Exchange.HOSE, Sector.ENERGY,
        "giá than, thủy văn hồ chứa, giá điện bán buôn EVN"),
    "VPB": SymbolInfo("VPB", "VPBank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "STB": SymbolInfo("STB", "Sacombank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, tái cơ cấu nợ xấu"),
    "SSI": SymbolInfo("SSI", "SSI Securities", Exchange.HOSE, Sector.FINANCIALS,
        "thanh khoản thị trường, margin lending, phí môi giới, VN-Index trend"),
    "TPB": SymbolInfo("TPB", "TPBank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "GVR": SymbolInfo("GVR", "Vietnam Rubber Group", Exchange.HOSE, Sector.MATERIALS,
        "giá cao su tự nhiên, FDI vào chế biến, tỷ giá USD"),
    "BCM": SymbolInfo("BCM", "Becamex IDC", Exchange.HOSE, Sector.REAL_ESTATE,
        "lãi suất vay mua nhà, tỷ lệ lấp đầy KCN, FDI inflow"),
    "PDR": SymbolInfo("PDR", "Phat Dat Real Estate", Exchange.HOSE, Sector.REAL_ESTATE,
        "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "KDH": SymbolInfo("KDH", "Khang Dien House", Exchange.HOSE, Sector.REAL_ESTATE,
        "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "BVH": SymbolInfo("BVH", "Bao Viet Holdings", Exchange.HOSE, Sector.FINANCIALS,
        "phí bảo hiểm, tỷ lệ bồi thường, lợi suất đầu tư tài chính"),
    "REE": SymbolInfo("REE", "REE Corporation", Exchange.HOSE, Sector.UTILITIES,
        "thủy văn hồ chứa, giá điện, công suất năng lượng tái tạo"),
    "PNJ": SymbolInfo("PNJ", "Phu Nhuan Jewelry", Exchange.HOSE, Sector.CONSUMER_GOODS,
        "giá vàng, sức mua tiêu dùng, tốc độ mở rộng cửa hàng"),
    "HDB": SymbolInfo("HDB", "HDBank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    # ── HOSE — Ngoài VN30 ──────────────────────────────────────────────
    "LPB": SymbolInfo("LPB", "LienVietPostBank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "EIB": SymbolInfo("EIB", "Eximbank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, tái cơ cấu nội bộ"),
    "OCB": SymbolInfo("OCB", "Orient Commercial Bank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VIB": SymbolInfo("VIB", "Vietnam International Bank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, cho vay mua ô tô và BĐS, room tín dụng"),
    "MSB": SymbolInfo("MSB", "MSB Bank", Exchange.HOSE, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VND": SymbolInfo("VND", "VNDIRECT", Exchange.HOSE, Sector.FINANCIALS,
        "thanh khoản thị trường, margin lending, phí môi giới, VN-Index trend"),
    "HCM": SymbolInfo("HCM", "Ho Chi Minh City Securities", Exchange.HOSE, Sector.FINANCIALS,
        "thanh khoản thị trường, margin lending, phí môi giới, VN-Index trend"),
    "VCI": SymbolInfo("VCI", "Viet Capital Securities", Exchange.HOSE, Sector.FINANCIALS,
        "thanh khoản thị trường, IB deals, margin lending, VN-Index trend"),
    "NVL": SymbolInfo("NVL", "Novaland", Exchange.HOSE, Sector.REAL_ESTATE,
        "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án, đòn bẩy tài chính"),
    "DXG": SymbolInfo("DXG", "Dat Xanh Group", Exchange.HOSE, Sector.REAL_ESTATE,
        "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "DGC": SymbolInfo("DGC", "Duc Giang Chemicals", Exchange.HOSE, Sector.MATERIALS,
        "giá phốt pho vàng, giá phân bón, xuất khẩu hóa chất"),
    "HSG": SymbolInfo("HSG", "Hoa Sen Group", Exchange.HOSE, Sector.MATERIALS,
        "giá thép cán nguội, biên lợi nhuận gia công, xuất khẩu"),
    "CTD": SymbolInfo("CTD", "Coteccons", Exchange.HOSE, Sector.INDUSTRIALS,
        "backlog hợp đồng xây dựng, biên lợi nhuận, tiến độ giải ngân đầu tư công"),
    "GMD": SymbolInfo("GMD", "Gemadept", Exchange.HOSE, Sector.INDUSTRIALS,
        "sản lượng container, phí cảng, tăng trưởng xuất khẩu"),
    "HAH": SymbolInfo("HAH", "Hai An Transport", Exchange.HOSE, Sector.INDUSTRIALS,
        "cước vận tải nội địa, sản lượng container, tăng trưởng xuất nhập khẩu"),
    "PVD": SymbolInfo("PVD", "PetroVietnam Drilling", Exchange.HOSE, Sector.ENERGY,
        "giá dầu Brent, rig day rate, capex E&P khu vực"),
    "PVS": SymbolInfo("PVS", "PetroVietnam Technical Services", Exchange.HNX, Sector.ENERGY,
        "giá dầu Brent, backlog dịch vụ kỹ thuật, capex upstream"),
    "BSR": SymbolInfo("BSR", "Binh Son Refinery", Exchange.UPCOM, Sector.ENERGY,
        "spread lọc dầu, giá dầu Brent, crack spread"),
    "FRT": SymbolInfo("FRT", "FPT Retail", Exchange.HOSE, Sector.CONSUMER_GOODS,
        "sức mua điện tử tiêu dùng, tỷ lệ mở rộng nhà thuốc Long Châu, biên lợi nhuận"),
    "VHC": SymbolInfo("VHC", "Vinh Hoan Seafood", Exchange.HOSE, Sector.CONSUMER_GOODS,
        "giá cá tra xuất khẩu, tỷ giá USD, thuế chống bán phá giá Mỹ"),
    "IMP": SymbolInfo("IMP", "Imexpharm", Exchange.HOSE, Sector.HEALTHCARE,
        "đấu thầu thuốc bệnh viện, chính sách dược, tỷ lệ thuốc kênh ETC"),
    "REE": SymbolInfo("REE", "REE Corporation", Exchange.HOSE, Sector.UTILITIES,
        "thủy văn hồ chứa, giá điện, công suất năng lượng tái tạo"),
    "GEX": SymbolInfo("GEX", "Gelex Group", Exchange.HOSE, Sector.UTILITIES,
        "giá điện, tiến độ dự án năng lượng tái tạo, tỷ lệ lấp đầy KCN"),
    "MSR": SymbolInfo("MSR", "Masan High-Tech Materials", Exchange.HOSE, Sector.MATERIALS,
        "giá vonfram, nhu cầu công nghiệp toàn cầu, xuất khẩu khoáng sản"),
    "VCS": SymbolInfo("VCS", "Vicostone", Exchange.HNX, Sector.MATERIALS,
        "giá thạch anh, xuất khẩu đá nhân tạo, thị trường bất động sản Mỹ"),
    # ── HNX ────────────────────────────────────────────────────────────
    "SHB": SymbolInfo("SHB", "SHB Bank", Exchange.HNX, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "MBS": SymbolInfo("MBS", "MB Securities", Exchange.HNX, Sector.FINANCIALS,
        "thanh khoản thị trường, margin lending, phí môi giới"),
    "TNG": SymbolInfo("TNG", "TNG Investment and Trading", Exchange.HNX, Sector.INDUSTRIALS,
        "đơn hàng dệt may xuất khẩu, tỷ giá USD, giá bông"),
    "IDC": SymbolInfo("IDC", "IDICO Corp", Exchange.HNX, Sector.REAL_ESTATE,
        "tỷ lệ lấp đầy KCN, FDI inflow, hạ tầng khu công nghiệp"),
    # ── UPCoM ──────────────────────────────────────────────────────────
    "VGI": SymbolInfo("VGI", "Viettel Global", Exchange.UPCOM, Sector.TELECOMS,
        "tăng trưởng thuê bao quốc tế, doanh thu viễn thông châu Phi/Myanmar, tỷ giá"),
    "ACV": SymbolInfo("ACV", "Airports Corporation of Vietnam", Exchange.UPCOM, Sector.INDUSTRIALS,
        "lượng hành khách, phí dịch vụ sân bay, capex mở rộng"),
    "MCH": SymbolInfo("MCH", "Masan Consumer Holdings", Exchange.UPCOM, Sector.CONSUMER_GOODS,
        "sức mua tiêu dùng, giá nguyên liệu, thị phần FMCG"),
    "SSB": SymbolInfo("SSB", "SeABank", Exchange.UPCOM, Sector.BANKING,
        "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
}


class SymbolNotFoundError(Exception):
    """Raised when a ticker is not found in the registry."""


class SymbolRegistry:
    """Dynamic symbol registry engine.

    Holds an in-memory cache populated from:
      1. static seed (always)
      2. VNDirect listing HTTP (on initialize)
      3. DB tickers (on initialize)

    After initialize(), all reads are O(1) dict lookups — no DB or HTTP on
    every call. Async refresh runs every TTL_HOURS.

    Thread/asyncio safety: _cache writes happen only in initialize() which
    is awaited at startup (single-writer pattern). Runtime enrich() is
    called from bot event handlers (single-threaded asyncio) — safe.
    """

    def __init__(self) -> None:
        # Start with static seed so registry is usable before initialize()
        self._cache: dict[str, SymbolInfo] = dict(_STATIC_SEED)
        self._initialized_at: datetime | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Async lifecycle
    # ------------------------------------------------------------------

    async def initialize(
        self,
        session_factory: Callable[[], Any] | None = None,
        force: bool = False,
    ) -> None:
        """Populate cache from HTTP + DB. Safe to call multiple times.

        Args:
            session_factory: Async context manager factory for DB session.
                             Pass None to skip DB enrichment.
            force:           If True, refresh even within TTL window.
        """
        async with self._lock:
            if not force and self._initialized_at is not None:
                age = datetime.now(UTC) - self._initialized_at
                if age < timedelta(hours=_TTL_HOURS):
                    logger.debug(
                        "registry.initialize.skipped",
                        age_hours=age.total_seconds() / 3600,
                    )
                    return

            logger.info("registry.initialize.start", seed_count=len(_STATIC_SEED))
            try:
                from src.market.registry_loader import RegistryLoader
                loader = RegistryLoader(session_factory=session_factory)
                merged = await loader.load(static_seed=_STATIC_SEED)
                self._cache = merged
                self._initialized_at = datetime.now(UTC)
                logger.info(
                    "registry.initialize.done",
                    total=len(self._cache),
                    static=len(_STATIC_SEED),
                    dynamic=len(self._cache) - len(_STATIC_SEED),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "registry.initialize.failed",
                    error=str(exc),
                    fallback="static seed only",
                )
                self._cache = dict(_STATIC_SEED)
                self._initialized_at = datetime.now(UTC)

    async def refresh(self, session_factory: Callable[[], Any] | None = None) -> None:
        """Force refresh from HTTP + DB regardless of TTL."""
        await self.initialize(session_factory=session_factory, force=True)

    # ------------------------------------------------------------------
    # Runtime enrich — called when user adds watchlist/thesis/position
    # ------------------------------------------------------------------

    def enrich(
        self,
        ticker: str,
        *,
        name: str | None = None,
        sector: "Sector | None" = None,
        exchange: "Exchange | None" = None,
        key_metrics: str | None = None,
    ) -> None:
        """Register or update a ticker at runtime.

        Safe to call from any async context. Uses existing data as baseline
        and only overrides fields that are explicitly provided.

        Example (called when user creates a new thesis for an unknown ticker):
            registry.enrich("XYZ", name="XYZ Corp", sector=Sector.TECHNOLOGY)
        """
        ticker = ticker.upper()
        existing = self._cache.get(ticker)
        self._cache[ticker] = SymbolInfo(
            ticker=ticker,
            name=name or (existing.name if existing else ticker),
            exchange=exchange or (existing.exchange if existing else Exchange.HOSE),
            sector=sector or (existing.sector if existing else Sector.OTHER),
            key_metrics=key_metrics or (existing.key_metrics if existing else ""),
        )

    # ------------------------------------------------------------------
    # Sync reads — O(1) after initialize()
    # ------------------------------------------------------------------

    def resolve(self, ticker: str) -> SymbolInfo:
        """Return SymbolInfo or raise SymbolNotFoundError."""
        info = self._cache.get(ticker.upper())
        if info is None:
            raise SymbolNotFoundError(f"Ticker '{ticker}' not found in registry")
        return info

    def get(self, ticker: str) -> SymbolInfo | None:
        """Return SymbolInfo or None — never raises."""
        return self._cache.get(ticker.upper())

    def exists(self, ticker: str) -> bool:
        return ticker.upper() in self._cache

    def list_by_exchange(self, exchange: Exchange) -> list[SymbolInfo]:
        return [s for s in self._cache.values() if s.exchange == exchange]

    def list_by_sector(self, sector: Sector) -> list[SymbolInfo]:
        return [s for s in self._cache.values() if s.sector == sector]

    def all_tickers(self) -> list[str]:
        return list(self._cache.keys())

    def list_all(self) -> list[SymbolInfo]:
        """Return all SymbolInfo entries regardless of exchange."""
        return list(self._cache.values())

    def size(self) -> int:
        return len(self._cache)

    def get_sector_map(self) -> dict[str, list[str]]:
        """Return mapping sector_name → list[ticker].

        Used by SectorRotationService to aggregate quotes per sector.
        """
        result: dict[str, list[str]] = {}
        for info in self._cache.values():
            result.setdefault(info.sector.value, []).append(info.ticker)
        return result

    def get_sector_context_str(self, ticker: str) -> str:
        """Return formatted sector context string for AI prompt injection.

        Returns empty string if ticker not found or key_metrics not populated.
        Example: 'Sector: Banking — metrics cần theo dõi: NIM, NPL ratio.'
        """
        info = self._cache.get(ticker.upper())
        if info is None or not info.key_metrics:
            return ""
        return f"Sector: {info.sector.value} — metrics cần theo dõi: {info.key_metrics}."

    # ------------------------------------------------------------------
    # Backward-compat shim — breadth_service used registry._REGISTRY
    # ------------------------------------------------------------------

    @property
    def _REGISTRY(self) -> dict[str, SymbolInfo]:  # noqa: N802
        """Deprecated direct dict access — use list_by_exchange() instead.

        Kept for backward compatibility with breadth_service.py.
        Will be removed after breadth_service is updated.
        """
        return self._cache


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
registry = SymbolRegistry()
