"""Symbol registry for Vietnamese equity markets.

Owner: market segment.
Source of truth for ticker → exchange/sector/name mapping.
Wave 2 will populate this from a real data file or API.
"""
from dataclasses import dataclass
from enum import Enum


class Exchange(str, Enum):
    HOSE = "HOSE"
    HNX = "HNX"
    UPCOM = "UPCOM"


class Sector(str, Enum):
    BANKING = "Banking"
    REAL_ESTATE = "Real Estate"
    CONSUMER_GOODS = "Consumer Goods"
    INDUSTRIALS = "Industrials"
    TECHNOLOGY = "Technology"
    ENERGY = "Energy"
    MATERIALS = "Materials"
    HEALTHCARE = "Healthcare"
    UTILITIES = "Utilities"
    FINANCIALS = "Financials"
    TELECOMS = "Telecoms"
    OTHER = "Other"


@dataclass(frozen=True)
class SymbolInfo:
    ticker: str
    name: str
    exchange: Exchange
    sector: Sector


# ---------------------------------------------------------------------------
# In-memory registry — will be replaced by DB/API lookup in Wave 2
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, SymbolInfo] = {
    # HOSE — Blue chips
    "VNM": SymbolInfo("VNM", "Vinamilk", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "VIC": SymbolInfo("VIC", "Vingroup", Exchange.HOSE, Sector.REAL_ESTATE),
    "VHM": SymbolInfo("VHM", "Vinhomes", Exchange.HOSE, Sector.REAL_ESTATE),
    "HPG": SymbolInfo("HPG", "Hoa Phat Group", Exchange.HOSE, Sector.MATERIALS),
    "VCB": SymbolInfo("VCB", "Vietcombank", Exchange.HOSE, Sector.BANKING),
    "BID": SymbolInfo("BID", "BIDV", Exchange.HOSE, Sector.BANKING),
    "CTG": SymbolInfo("CTG", "VietinBank", Exchange.HOSE, Sector.BANKING),
    "TCB": SymbolInfo("TCB", "Techcombank", Exchange.HOSE, Sector.BANKING),
    "MBB": SymbolInfo("MBB", "MB Bank", Exchange.HOSE, Sector.BANKING),
    "VPB": SymbolInfo("VPB", "VPBank", Exchange.HOSE, Sector.BANKING),
    "MSN": SymbolInfo("MSN", "Masan Group", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "MWG": SymbolInfo("MWG", "The Gioi Di Dong", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "FPT": SymbolInfo("FPT", "FPT Corporation", Exchange.HOSE, Sector.TECHNOLOGY),
    "GAS": SymbolInfo("GAS", "PetroVietnam Gas", Exchange.HOSE, Sector.ENERGY),
    "PLX": SymbolInfo("PLX", "Petrolimex", Exchange.HOSE, Sector.ENERGY),
    "PNJ": SymbolInfo("PNJ", "Phu Nhuan Jewelry", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "REE": SymbolInfo("REE", "REE Corporation", Exchange.HOSE, Sector.UTILITIES),
    "SSI": SymbolInfo("SSI", "SSI Securities", Exchange.HOSE, Sector.FINANCIALS),
    "VND": SymbolInfo("VND", "VNDIRECT", Exchange.HOSE, Sector.FINANCIALS),
    "HDB": SymbolInfo("HDB", "HDBank", Exchange.HOSE, Sector.BANKING),
    # HNX
    "SHB": SymbolInfo("SHB", "SHB Bank", Exchange.HNX, Sector.BANKING),
    "NVB": SymbolInfo("NVB", "NVB Bank", Exchange.HNX, Sector.BANKING),
    # UPCoM
    "ACV": SymbolInfo("ACV", "Airports Corporation", Exchange.UPCOM, Sector.INDUSTRIALS),
    "VGT": SymbolInfo("VGT", "Vietnam National Textile", Exchange.UPCOM, Sector.INDUSTRIALS),
}


class SymbolNotFoundError(Exception):
    """Raised when a ticker is not found in the registry."""


class SymbolRegistry:
    """Lookup service for VN stock symbols.

    Wave 2: replace _REGISTRY with async DB/API lookup.
    """

    def resolve(self, ticker: str) -> SymbolInfo:
        """Return SymbolInfo or raise SymbolNotFoundError."""
        info = _REGISTRY.get(ticker.upper())
        if info is None:
            raise SymbolNotFoundError(f"Ticker '{ticker}' not found in registry")
        return info

    def exists(self, ticker: str) -> bool:
        return ticker.upper() in _REGISTRY

    def list_by_exchange(self, exchange: Exchange) -> list[SymbolInfo]:
        return [s for s in _REGISTRY.values() if s.exchange == exchange]

    def list_by_sector(self, sector: Sector) -> list[SymbolInfo]:
        return [s for s in _REGISTRY.values() if s.sector == sector]

    def all_tickers(self) -> list[str]:
        return list(_REGISTRY.keys())


# Module-level singleton
registry = SymbolRegistry()
