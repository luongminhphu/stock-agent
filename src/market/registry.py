"""Symbol registry for Vietnamese equity markets.

Owner: market segment.
Source of truth for ticker → exchange/sector/name mapping.
Wave 3 will load this from a CSV/DB instead of hardcode.
"""

from dataclasses import dataclass
from enum import StrEnum


class Exchange(StrEnum):
    HOSE = "HOSE"
    HNX = "HNX"
    UPCOM = "UPCOM"


class Sector(StrEnum):
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
# In-memory registry — Wave 3 sẽ thay bằng CSV/DB lookup
# Covers: VN30 Q1/2026 đầy đủ + top thanh khoản HOSE + HNX blue-chip + UPCoM vốn hóa lớn
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, SymbolInfo] = {
    # ── HOSE — VN30 (Q1/2026, đầy đủ 30 mã) ───────────────────────────
    "VCB": SymbolInfo("VCB", "Vietcombank", Exchange.HOSE, Sector.BANKING),
    "VHM": SymbolInfo("VHM", "Vinhomes", Exchange.HOSE, Sector.REAL_ESTATE),
    "VIC": SymbolInfo("VIC", "Vingroup", Exchange.HOSE, Sector.REAL_ESTATE),
    "FPT": SymbolInfo("FPT", "FPT Corporation", Exchange.HOSE, Sector.TECHNOLOGY),
    "BID": SymbolInfo("BID", "BIDV", Exchange.HOSE, Sector.BANKING),
    "HPG": SymbolInfo("HPG", "Hoa Phat Group", Exchange.HOSE, Sector.MATERIALS),
    "GAS": SymbolInfo("GAS", "PetroVietnam Gas", Exchange.HOSE, Sector.ENERGY),
    "CTG": SymbolInfo("CTG", "VietinBank", Exchange.HOSE, Sector.BANKING),
    "TCB": SymbolInfo("TCB", "Techcombank", Exchange.HOSE, Sector.BANKING),
    "MBB": SymbolInfo("MBB", "MB Bank", Exchange.HOSE, Sector.BANKING),
    "MSN": SymbolInfo("MSN", "Masan Group", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "VRE": SymbolInfo("VRE", "Vincom Retail", Exchange.HOSE, Sector.REAL_ESTATE),
    "SAB": SymbolInfo("SAB", "Sabeco", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "ACB": SymbolInfo("ACB", "Asia Commercial Bank", Exchange.HOSE, Sector.BANKING),
    "VNM": SymbolInfo("VNM", "Vinamilk", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "MWG": SymbolInfo("MWG", "The Gioi Di Dong", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "PLX": SymbolInfo("PLX", "Petrolimex", Exchange.HOSE, Sector.ENERGY),
    "POW": SymbolInfo("POW", "PetroVietnam Power", Exchange.HOSE, Sector.ENERGY),
    "VPB": SymbolInfo("VPB", "VPBank", Exchange.HOSE, Sector.BANKING),
    "STB": SymbolInfo("STB", "Sacombank", Exchange.HOSE, Sector.BANKING),
    "SSI": SymbolInfo("SSI", "SSI Securities", Exchange.HOSE, Sector.FINANCIALS),
    "TPB": SymbolInfo("TPB", "TPBank", Exchange.HOSE, Sector.BANKING),
    "GVR": SymbolInfo("GVR", "Vietnam Rubber Group", Exchange.HOSE, Sector.MATERIALS),
    "BCM": SymbolInfo("BCM", "Becamex IDC", Exchange.HOSE, Sector.REAL_ESTATE),
    "PDR": SymbolInfo("PDR", "Phat Dat Real Estate", Exchange.HOSE, Sector.REAL_ESTATE),
    "KDH": SymbolInfo("KDH", "Khang Dien House", Exchange.HOSE, Sector.REAL_ESTATE),
    "BVH": SymbolInfo("BVH", "Bao Viet Holdings", Exchange.HOSE, Sector.FINANCIALS),
    "REE": SymbolInfo("REE", "REE Corporation", Exchange.HOSE, Sector.UTILITIES),
    "PNJ": SymbolInfo("PNJ", "Phu Nhuan Jewelry", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "HDB": SymbolInfo("HDB", "HDBank", Exchange.HOSE, Sector.BANKING),
    # ── HOSE — Ngoài VN30, thanh khoản cao / vốn hóa đáng kể ──────────
    # Ngân hàng
    "LPB": SymbolInfo("LPB", "LienVietPostBank", Exchange.HOSE, Sector.BANKING),
    "EIB": SymbolInfo("EIB", "Eximbank", Exchange.HOSE, Sector.BANKING),
    "OCB": SymbolInfo("OCB", "Orient Commercial Bank", Exchange.HOSE, Sector.BANKING),
    "VIB": SymbolInfo("VIB", "Vietnam International Bank", Exchange.HOSE, Sector.BANKING),
    "MSB": SymbolInfo("MSB", "MSB Bank", Exchange.HOSE, Sector.BANKING),
    # Chứng khoán / Tài chính
    "VND": SymbolInfo("VND", "VNDIRECT", Exchange.HOSE, Sector.FINANCIALS),
    "HCM": SymbolInfo("HCM", "Ho Chi Minh City Securities", Exchange.HOSE, Sector.FINANCIALS),
    "VCI": SymbolInfo("VCI", "Viet Capital Securities", Exchange.HOSE, Sector.FINANCIALS),
    "TCX": SymbolInfo("TCX", "TCBS (Techcom Securities)", Exchange.HOSE, Sector.FINANCIALS),
    "AGR": SymbolInfo("AGR", "Agribank Securities", Exchange.HOSE, Sector.FINANCIALS),
    # Bất động sản
    "NVL": SymbolInfo("NVL", "Novaland", Exchange.HOSE, Sector.REAL_ESTATE),
    "DXG": SymbolInfo("DXG", "Dat Xanh Group", Exchange.HOSE, Sector.REAL_ESTATE),
    "DIG": SymbolInfo("DIG", "DIC Corp", Exchange.HOSE, Sector.REAL_ESTATE),
    "HDG": SymbolInfo("HDG", "Ha Do Group", Exchange.HOSE, Sector.REAL_ESTATE),
    "IJC": SymbolInfo("IJC", "Becamex IJC", Exchange.HOSE, Sector.REAL_ESTATE),
    # Vật liệu / Công nghiệp
    "DGC": SymbolInfo("DGC", "Duc Giang Chemicals", Exchange.HOSE, Sector.MATERIALS),
    "HSG": SymbolInfo("HSG", "Hoa Sen Group", Exchange.HOSE, Sector.MATERIALS),
    "NKG": SymbolInfo("NKG", "Nam Kim Steel", Exchange.HOSE, Sector.MATERIALS),
    "CTD": SymbolInfo("CTD", "Coteccons", Exchange.HOSE, Sector.INDUSTRIALS),
    "HHV": SymbolInfo("HHV", "Highway HHV", Exchange.HOSE, Sector.INDUSTRIALS),
    # Năng lượng / Dầu khí
    "PVD": SymbolInfo("PVD", "PetroVietnam Drilling", Exchange.HOSE, Sector.ENERGY),
    "PVS": SymbolInfo("PVS", "PetroVietnam Technical Services", Exchange.HNX, Sector.ENERGY),
    "BSR": SymbolInfo("BSR", "Binh Son Refinery", Exchange.UPCOM, Sector.ENERGY),
    # Công nghệ / Viễn thông
    "CMG": SymbolInfo("CMG", "CMC Technology Group", Exchange.HOSE, Sector.TECHNOLOGY),
    # Tiêu dùng / Bán lẻ
    "MCH": SymbolInfo("MCH", "Masan Consumer Holdings", Exchange.UPCOM, Sector.CONSUMER_GOODS),
    "MML": SymbolInfo("MML", "Masan MEATLife", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "FRT": SymbolInfo("FRT", "FPT Retail", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "DBC": SymbolInfo("DBC", "Dabaco Group", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "VHC": SymbolInfo("VHC", "Vinh Hoan Seafood", Exchange.HOSE, Sector.CONSUMER_GOODS),
    "ANV": SymbolInfo("ANV", "Nam Viet Seafood", Exchange.HOSE, Sector.CONSUMER_GOODS),
    # Y tế
    "DBD": SymbolInfo("DBD", "Binh Dinh Pharma", Exchange.HOSE, Sector.HEALTHCARE),
    "DVN": SymbolInfo("DVN", "Danapha Pharma", Exchange.UPCOM, Sector.HEALTHCARE),
    "IMP": SymbolInfo("IMP", "Imexpharm", Exchange.HOSE, Sector.HEALTHCARE),
    "DMC": SymbolInfo("DMC", "Domesco", Exchange.HOSE, Sector.HEALTHCARE),
    # Tiện ích / Điện
    "PC1": SymbolInfo("PC1", "Power Construction 1", Exchange.HOSE, Sector.UTILITIES),
    "GEX": SymbolInfo("GEX", "Gelex Group", Exchange.HOSE, Sector.UTILITIES),
    # Logistics / Vận tải
    "GMD": SymbolInfo("GMD", "Gemadept", Exchange.HOSE, Sector.INDUSTRIALS),
    "VSC": SymbolInfo("VSC", "Vietnam Container Shipping", Exchange.HOSE, Sector.INDUSTRIALS),
    "HAH": SymbolInfo("HAH", "Hai An Transport", Exchange.HOSE, Sector.INDUSTRIALS),
    "SFI": SymbolInfo("SFI", "SAFI Transport", Exchange.HOSE, Sector.INDUSTRIALS),
    # Khác
    "VPL": SymbolInfo("VPL", "Vinpearl", Exchange.HOSE, Sector.OTHER),
    # ── HNX — Blue-chip & thanh khoản cao ──────────────────────────────
    "SHB": SymbolInfo("SHB", "SHB Bank", Exchange.HNX, Sector.BANKING),
    "NVB": SymbolInfo("NVB", "NVB Bank", Exchange.HNX, Sector.BANKING),
    "MBS": SymbolInfo("MBS", "MB Securities", Exchange.HNX, Sector.FINANCIALS),
    "SHS": SymbolInfo("SHS", "Saigon-Hanoi Securities", Exchange.HNX, Sector.FINANCIALS),
    "CEO": SymbolInfo("CEO", "C.E.O Group", Exchange.HNX, Sector.REAL_ESTATE),
    "THD": SymbolInfo("THD", "Thaiholdings", Exchange.HNX, Sector.REAL_ESTATE),
    "TNG": SymbolInfo("TNG", "TNG Investment and Trading", Exchange.HNX, Sector.INDUSTRIALS),
    "PVB": SymbolInfo("PVB", "PetroVietnam Binh Son", Exchange.HNX, Sector.ENERGY),
    "TV2": SymbolInfo("TV2", "Power Engineering 2", Exchange.HNX, Sector.UTILITIES),
    "VCS": SymbolInfo("VCS", "Vicostone", Exchange.HNX, Sector.MATERIALS),
    "PVC": SymbolInfo("PVC", "PetroVietnam Coating", Exchange.HNX, Sector.MATERIALS),
    "IDC": SymbolInfo("IDC", "IDICO Corp", Exchange.HNX, Sector.REAL_ESTATE),
    # ── UPCoM — Vốn hóa lớn, tiềm năng chuyển sàn ─────────────────────
    "ACV": SymbolInfo("ACV", "Airports Corporation of Vietnam", Exchange.UPCOM, Sector.INDUSTRIALS),
    "VGI": SymbolInfo("VGI", "Viettel Global", Exchange.UPCOM, Sector.TELECOMS),
    "MSR": SymbolInfo("MSR", "Masan High-Tech Materials", Exchange.HOSE, Sector.MATERIALS),
    "MVN": SymbolInfo("MVN", "VIMC (Vietnam Maritime Corp)", Exchange.UPCOM, Sector.INDUSTRIALS),
    "VEA": SymbolInfo("VEA", "Vietnam Engine & Agricultural", Exchange.UPCOM, Sector.INDUSTRIALS),
    "VGT": SymbolInfo("VGT", "Vietnam National Textile", Exchange.UPCOM, Sector.INDUSTRIALS),
    "SSB": SymbolInfo("SSB", "SeABank", Exchange.UPCOM, Sector.BANKING),
    "BAB": SymbolInfo("BAB", "Bac A Bank", Exchange.UPCOM, Sector.BANKING),
    "KOS": SymbolInfo("KOS", "Kosy Group", Exchange.UPCOM, Sector.REAL_ESTATE),
}


class SymbolNotFoundError(Exception):
    """Raised when a ticker is not found in the registry."""


class SymbolRegistry:
    """Lookup service for VN stock symbols.

    Wave 3: replace _REGISTRY with async CSV/DB lookup.
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
