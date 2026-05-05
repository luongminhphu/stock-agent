"""Symbol registry for Vietnamese equity markets.

Owner: market segment.
Source of truth for ticker → exchange/sector/name/key_metrics mapping.
Wave 3 will load this from a CSV/DB instead of hardcode.
"""

from dataclasses import dataclass, field
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
    key_metrics: str = ""  # metrics nhà đầu tư cần theo dõi — dùng cho AI context injection


# ---------------------------------------------------------------------------
# In-memory registry — Wave 3 sẽ thay bằng CSV/DB lookup
# Covers: VN30 Q1/2026 đầy đủ + top thanh khoản HOSE + HNX blue-chip + UPCoM vốn hóa lớn
# key_metrics: populated for high-coverage tickers, "" = not yet enriched
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, SymbolInfo] = {
    # ── HOSE — VN30 (Q1/2026, đầy đủ 30 mã) ───────────────────────────
    "VCB": SymbolInfo("VCB", "Vietcombank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VHM": SymbolInfo("VHM", "Vinhomes", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án, tồn kho"),
    "VIC": SymbolInfo("VIC", "Vingroup", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tiến độ dự án VinFast, dòng tiền tập đoàn"),
    "FPT": SymbolInfo("FPT", "FPT Corporation", Exchange.HOSE, Sector.TECHNOLOGY,
        key_metrics="tăng trưởng IT outsourcing, biên lợi nhuận mảng nước ngoài, tỷ giá"),
    "BID": SymbolInfo("BID", "BIDV", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "HPG": SymbolInfo("HPG", "Hoa Phat Group", Exchange.HOSE, Sector.MATERIALS,
        key_metrics="giá thép HRC, giá quặng sắt, hoạt động xây dựng, xuất khẩu thép"),
    "GAS": SymbolInfo("GAS", "PetroVietnam Gas", Exchange.HOSE, Sector.ENERGY,
        key_metrics="giá khí LNG, nhu cầu điện, hợp đồng Petro Vietnam"),
    "CTG": SymbolInfo("CTG", "VietinBank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "TCB": SymbolInfo("TCB", "Techcombank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "MBB": SymbolInfo("MBB", "MB Bank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "MSN": SymbolInfo("MSN", "Masan Group", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="sức mua tiêu dùng, giá nguyên liệu đầu vào, tỷ giá"),
    "VRE": SymbolInfo("VRE", "Vincom Retail", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="tỷ lệ lấp đầy mặt bằng, sức mua tiêu dùng, mở rộng trung tâm thương mại"),
    "SAB": SymbolInfo("SAB", "Sabeco", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="sản lượng bia, sức mua tiêu dùng, thuế tiêu thụ đặc biệt"),
    "ACB": SymbolInfo("ACB", "Asia Commercial Bank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VNM": SymbolInfo("VNM", "Vinamilk", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="giá sữa bột nhập khẩu, sức mua, thị phần nội địa"),
    "MWG": SymbolInfo("MWG", "The Gioi Di Dong", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="sức mua tiêu dùng, tỷ lệ mở rộng cửa hàng, biên lợi nhuận"),
    "PLX": SymbolInfo("PLX", "Petrolimex", Exchange.HOSE, Sector.ENERGY,
        key_metrics="giá dầu thô, biên lợi nhuận kinh doanh xăng dầu, tỷ giá"),
    "POW": SymbolInfo("POW", "PetroVietnam Power", Exchange.HOSE, Sector.ENERGY,
        key_metrics="giá than, thủy văn hồ chứa, giá điện bán buôn EVN"),
    "VPB": SymbolInfo("VPB", "VPBank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "STB": SymbolInfo("STB", "Sacombank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, tái cơ cấu nợ xấu"),
    "SSI": SymbolInfo("SSI", "SSI Securities", Exchange.HOSE, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, margin lending, phí môi giới, VN-Index trend"),
    "TPB": SymbolInfo("TPB", "TPBank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "GVR": SymbolInfo("GVR", "Vietnam Rubber Group", Exchange.HOSE, Sector.MATERIALS,
        key_metrics="giá cao su tự nhiên, FDI vào chế biến, tỷ giá USD"),
    "BCM": SymbolInfo("BCM", "Becamex IDC", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ lấp đầy KCN, FDI inflow"),
    "PDR": SymbolInfo("PDR", "Phat Dat Real Estate", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "KDH": SymbolInfo("KDH", "Khang Dien House", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "BVH": SymbolInfo("BVH", "Bao Viet Holdings", Exchange.HOSE, Sector.FINANCIALS,
        key_metrics="phí bảo hiểm, tỷ lệ bồi thường, lợi suất đầu tư tài chính"),
    "REE": SymbolInfo("REE", "REE Corporation", Exchange.HOSE, Sector.UTILITIES,
        key_metrics="thủy văn hồ chứa, giá điện, công suất năng lượng tái tạo"),
    "PNJ": SymbolInfo("PNJ", "Phu Nhuan Jewelry", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="giá vàng, sức mua tiêu dùng, tốc độ mở rộng cửa hàng"),
    "HDB": SymbolInfo("HDB", "HDBank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    # ── HOSE — Ngoài VN30, thanh khoản cao / vốn hóa đáng kể ──────────
    # Ngân hàng
    "LPB": SymbolInfo("LPB", "LienVietPostBank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "EIB": SymbolInfo("EIB", "Eximbank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, tái cơ cấu nội bộ"),
    "OCB": SymbolInfo("OCB", "Orient Commercial Bank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VIB": SymbolInfo("VIB", "Vietnam International Bank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, cho vay mua ô tô và BĐS, room tín dụng"),
    "MSB": SymbolInfo("MSB", "MSB Bank", Exchange.HOSE, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    # Chứng khoán / Tài chính
    "VND": SymbolInfo("VND", "VNDIRECT", Exchange.HOSE, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, margin lending, phí môi giới, VN-Index trend"),
    "HCM": SymbolInfo("HCM", "Ho Chi Minh City Securities", Exchange.HOSE, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, margin lending, phí môi giới, VN-Index trend"),
    "VCI": SymbolInfo("VCI", "Viet Capital Securities", Exchange.HOSE, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, IB deals, margin lending, VN-Index trend"),
    "TCX": SymbolInfo("TCX", "TCBS (Techcom Securities)", Exchange.HOSE, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, margin lending, phí môi giới, VN-Index trend"),
    "AGR": SymbolInfo("AGR", "Agribank Securities", Exchange.HOSE, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, margin lending, phí môi giới"),
    # Bất động sản
    "NVL": SymbolInfo("NVL", "Novaland", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án, đòn bẩy tài chính"),
    "DXG": SymbolInfo("DXG", "Dat Xanh Group", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "DIG": SymbolInfo("DIG", "DIC Corp", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "HDG": SymbolInfo("HDG", "Ha Do Group", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án, mảng năng lượng"),
    "IJC": SymbolInfo("IJC", "Becamex IJC", Exchange.HOSE, Sector.REAL_ESTATE,
        key_metrics="tỷ lệ lấp đầy KCN, FDI inflow, hạ tầng Bình Dương"),
    # Vật liệu / Công nghiệp
    "DGC": SymbolInfo("DGC", "Duc Giang Chemicals", Exchange.HOSE, Sector.MATERIALS,
        key_metrics="giá phốt pho vàng, giá phân bón, xuất khẩu hóa chất"),
    "HSG": SymbolInfo("HSG", "Hoa Sen Group", Exchange.HOSE, Sector.MATERIALS,
        key_metrics="giá thép cán nguội, biên lợi nhuận gia công, xuất khẩu"),
    "NKG": SymbolInfo("NKG", "Nam Kim Steel", Exchange.HOSE, Sector.MATERIALS,
        key_metrics="giá thép cán nguội, biên lợi nhuận gia công, xuất khẩu"),
    "CTD": SymbolInfo("CTD", "Coteccons", Exchange.HOSE, Sector.INDUSTRIALS,
        key_metrics="backlog hợp đồng xây dựng, biên lợi nhuận, tiến độ giải ngân đầu tư công"),
    "HHV": SymbolInfo("HHV", "Highway HHV", Exchange.HOSE, Sector.INDUSTRIALS,
        key_metrics="lưu lượng xe, phí BOT, tiến độ dự án cao tốc"),
    # Năng lượng / Dầu khí
    "PVD": SymbolInfo("PVD", "PetroVietnam Drilling", Exchange.HOSE, Sector.ENERGY,
        key_metrics="giá dầu Brent, rig day rate, capex E&P khu vực"),
    "PVS": SymbolInfo("PVS", "PetroVietnam Technical Services", Exchange.HNX, Sector.ENERGY,
        key_metrics="giá dầu Brent, backlog dịch vụ kỹ thuật, capex upstream"),
    "BSR": SymbolInfo("BSR", "Binh Son Refinery", Exchange.UPCOM, Sector.ENERGY,
        key_metrics="spread lọc dầu, giá dầu Brent, crack spread"),
    # Công nghệ / Viễn thông
    "CMG": SymbolInfo("CMG", "CMC Technology Group", Exchange.HOSE, Sector.TECHNOLOGY,
        key_metrics="tăng trưởng IT services, cloud adoption, tỷ giá"),
    # Tiêu dùng / Bán lẻ
    "MCH": SymbolInfo("MCH", "Masan Consumer Holdings", Exchange.UPCOM, Sector.CONSUMER_GOODS,
        key_metrics="sức mua tiêu dùng, giá nguyên liệu, thị phần FMCG"),
    "MML": SymbolInfo("MML", "Masan MEATLife", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="giá heo hơi, sức mua tiêu dùng, biên lợi nhuận thịt chế biến"),
    "FRT": SymbolInfo("FRT", "FPT Retail", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="sức mua điện tử tiêu dùng, tỷ lệ mở rộng nhà thuốc Long Châu, biên lợi nhuận"),
    "DBC": SymbolInfo("DBC", "Dabaco Group", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="giá heo hơi, giá thức ăn chăn nuôi, sức mua tiêu dùng"),
    "VHC": SymbolInfo("VHC", "Vinh Hoan Seafood", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="giá cá tra xuất khẩu, tỷ giá USD, thuế chống bán phá giá Mỹ"),
    "ANV": SymbolInfo("ANV", "Nam Viet Seafood", Exchange.HOSE, Sector.CONSUMER_GOODS,
        key_metrics="giá cá tra xuất khẩu, tỷ giá USD, thuế chống bán phá giá"),
    # Y tế
    "DBD": SymbolInfo("DBD", "Binh Dinh Pharma", Exchange.HOSE, Sector.HEALTHCARE,
        key_metrics="đấu thầu thuốc bệnh viện, chính sách dược, biên lợi nhuận sản xuất"),
    "DVN": SymbolInfo("DVN", "Danapha Pharma", Exchange.UPCOM, Sector.HEALTHCARE,
        key_metrics="đấu thầu thuốc bệnh viện, chính sách dược, biên lợi nhuận sản xuất"),
    "IMP": SymbolInfo("IMP", "Imexpharm", Exchange.HOSE, Sector.HEALTHCARE,
        key_metrics="đấu thầu thuốc bệnh viện, chính sách dược, tỷ lệ thuốc kênh ETC"),
    "DMC": SymbolInfo("DMC", "Domesco", Exchange.HOSE, Sector.HEALTHCARE,
        key_metrics="đấu thầu thuốc bệnh viện, chính sách dược, biên lợi nhuận sản xuất"),
    # Tiện ích / Điện
    "PC1": SymbolInfo("PC1", "Power Construction 1", Exchange.HOSE, Sector.UTILITIES,
        key_metrics="tiến độ dự án điện, giá điện, vốn đầu tư công"),
    "GEX": SymbolInfo("GEX", "Gelex Group", Exchange.HOSE, Sector.UTILITIES,
        key_metrics="giá điện, tiến độ dự án năng lượng tái tạo, tỷ lệ lấp đầy KCN"),
    # Logistics / Vận tải
    "GMD": SymbolInfo("GMD", "Gemadept", Exchange.HOSE, Sector.INDUSTRIALS,
        key_metrics="sản lượng container, phí cảng, tăng trưởng xuất khẩu"),
    "VSC": SymbolInfo("VSC", "Vietnam Container Shipping", Exchange.HOSE, Sector.INDUSTRIALS,
        key_metrics="cước container, sản lượng hàng hóa, tăng trưởng xuất nhập khẩu"),
    "HAH": SymbolInfo("HAH", "Hai An Transport", Exchange.HOSE, Sector.INDUSTRIALS,
        key_metrics="cước vận tải nội địa, sản lượng container, tăng trưởng xuất nhập khẩu"),
    "SFI": SymbolInfo("SFI", "SAFI Transport", Exchange.HOSE, Sector.INDUSTRIALS,
        key_metrics="cước vận tải, sản lượng hàng hóa, chi phí nhiên liệu"),
    # Hàng không
    "HVN": SymbolInfo("HVN", "Vietnam Airlines", Exchange.HOSE, Sector.INDUSTRIALS,
        key_metrics="giá nhiên liệu jet, phục hồi du lịch quốc tế, tỷ giá USD"),
    "ACV": SymbolInfo("ACV", "Airports Corporation of Vietnam", Exchange.UPCOM, Sector.INDUSTRIALS,
        key_metrics="lượng hành khách, phí dịch vụ sân bay, capex mở rộng"),
    # Khác
    "VPL": SymbolInfo("VPL", "Vinpearl", Exchange.HOSE, Sector.OTHER),
    # ── HNX — Blue-chip & thanh khoản cao ──────────────────────────────
    "SHB": SymbolInfo("SHB", "SHB Bank", Exchange.HNX, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "NVB": SymbolInfo("NVB", "NVB Bank", Exchange.HNX, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, tái cơ cấu"),
    "MBS": SymbolInfo("MBS", "MB Securities", Exchange.HNX, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, margin lending, phí môi giới"),
    "SHS": SymbolInfo("SHS", "Saigon-Hanoi Securities", Exchange.HNX, Sector.FINANCIALS,
        key_metrics="thanh khoản thị trường, margin lending, phí môi giới"),
    "CEO": SymbolInfo("CEO", "C.E.O Group", Exchange.HNX, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, pháp lý dự án, tỷ lệ hấp thụ"),
    "THD": SymbolInfo("THD", "Thaiholdings", Exchange.HNX, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, pháp lý dự án, dòng tiền tập đoàn"),
    "TNG": SymbolInfo("TNG", "TNG Investment and Trading", Exchange.HNX, Sector.INDUSTRIALS,
        key_metrics="đơn hàng dệt may xuất khẩu, tỷ giá USD, giá bông"),
    "PVB": SymbolInfo("PVB", "PetroVietnam Binh Son", Exchange.HNX, Sector.ENERGY,
        key_metrics="giá dầu Brent, spread lọc dầu, crack spread"),
    "TV2": SymbolInfo("TV2", "Power Engineering 2", Exchange.HNX, Sector.UTILITIES,
        key_metrics="tiến độ dự án điện, vốn đầu tư công, giá điện"),
    "VCS": SymbolInfo("VCS", "Vicostone", Exchange.HNX, Sector.MATERIALS,
        key_metrics="giá thạch anh, xuất khẩu đá nhân tạo, thị trường bất động sản Mỹ"),
    "PVC": SymbolInfo("PVC", "PetroVietnam Coating", Exchange.HNX, Sector.MATERIALS,
        key_metrics="giá sơn/hóa chất bảo vệ, backlog dự án dầu khí"),
    "IDC": SymbolInfo("IDC", "IDICO Corp", Exchange.HNX, Sector.REAL_ESTATE,
        key_metrics="tỷ lệ lấp đầy KCN, FDI inflow, hạ tầng khu công nghiệp"),
    # ── UPCoM — Vốn hóa lớn, tiềm năng chuyển sàn ─────────────────────
    "VGI": SymbolInfo("VGI", "Viettel Global", Exchange.UPCOM, Sector.TELECOMS,
        key_metrics="tăng trưởng thuê bao quốc tế, doanh thu viễn thông châu Phi/Myanmar, tỷ giá"),
    "MSR": SymbolInfo("MSR", "Masan High-Tech Materials", Exchange.HOSE, Sector.MATERIALS,
        key_metrics="giá vonfram, nhu cầu công nghiệp toàn cầu, xuất khẩu khoáng sản"),
    "MVN": SymbolInfo("MVN", "VIMC (Vietnam Maritime Corp)", Exchange.UPCOM, Sector.INDUSTRIALS,
        key_metrics="cước vận tải biển, sản lượng hàng hóa, giá nhiên liệu tàu"),
    "VEA": SymbolInfo("VEA", "Vietnam Engine & Agricultural", Exchange.UPCOM, Sector.INDUSTRIALS,
        key_metrics="doanh số xe máy Honda/Toyota, sức mua tiêu dùng, cổ tức từ liên doanh"),
    "VGT": SymbolInfo("VGT", "Vietnam National Textile", Exchange.UPCOM, Sector.INDUSTRIALS,
        key_metrics="đơn hàng dệt may xuất khẩu, giá bông, tỷ giá USD"),
    "SSB": SymbolInfo("SSB", "SeABank", Exchange.UPCOM, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "BAB": SymbolInfo("BAB", "Bac A Bank", Exchange.UPCOM, Sector.BANKING,
        key_metrics="NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "KOS": SymbolInfo("KOS", "Kosy Group", Exchange.UPCOM, Sector.REAL_ESTATE,
        key_metrics="lãi suất vay mua nhà, pháp lý dự án, tỷ lệ hấp thụ"),
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

    def get_sector_map(self) -> dict[str, list[str]]:
        """Return mapping sector_name → list[ticker].

        Used by SectorRotationService to aggregate quotes per sector.
        """
        result: dict[str, list[str]] = {}
        for info in _REGISTRY.values():
            result.setdefault(info.sector.value, []).append(info.ticker)
        return result

    def get_sector_context_str(self, ticker: str) -> str:
        """Return formatted sector context string for AI prompt injection.

        Returns empty string if ticker not found or key_metrics not populated.
        Callers should handle empty string gracefully (no context = no injection).

        Example output:
            "Sector: Banking — metrics cần theo dõi: NIM, NPL ratio, room tín dụng."
        """
        info = _REGISTRY.get(ticker.upper())
        if info is None or not info.key_metrics:
            return ""
        return f"Sector: {info.sector.value} — metrics cần theo dõi: {info.key_metrics}."


# Module-level singleton
registry = SymbolRegistry()
