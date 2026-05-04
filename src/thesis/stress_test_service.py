"""Stress-Test Service — orchestrate thesis stress-test flow.

Owner: thesis segment.

Responsibilities:
- Load active thesis + assumptions + catalysts from DB
- Fetch current price from market segment
- Build macro_context string (price + sector hint) for AI
- Call StressTestAgent
- Return StressTestOutput to caller (bot adapter)

Non-responsibilities:
- No DB writes — stress-test is read-only, does not mutate thesis state
- No Discord formatting (formatter lives in bot layer)
- No business rule decisions (invalidation threshold lives in ReviewService)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.stress_test import StressTestAgent
from src.ai.schemas import StressTestOutput
from src.platform.logging import get_logger
from src.thesis.repository import ThesisRepository

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Sector context helpers
# ---------------------------------------------------------------------------

_SECTOR_HINTS: dict[str, tuple[str, str]] = {
    # ticker -> (sector_label, key_metrics_to_watch)
    # Banking
    "VCB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "BID":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "CTG":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "TCB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "MBB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "VPB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "HDB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "ACB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "STB":  ("Banking", "NIM, NPL ratio, room tín dụng, tái cơ cấu nợ xấu"),
    "LPB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    "MSB":  ("Banking", "NIM, NPL ratio, room tín dụng, lãi suất điều hành NHNN"),
    # Real Estate
    "VHM":  ("Real Estate", "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án, tồn kho"),
    "NVL":  ("Real Estate", "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án, đòn bẩy tài chính"),
    "PDR":  ("Real Estate", "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "DXG":  ("Real Estate", "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "KDH":  ("Real Estate", "lãi suất vay mua nhà, tỷ lệ hấp thụ, pháp lý dự án"),
    "BCM":  ("Real Estate / KCN", "lãi suất vay mua nhà, tỷ lệ lấp đầy KCN, FDI inflow"),
    # Industrial Park / FDI
    "GVR":  ("Industrial / Rubber", "giá cao su tự nhiên, FDI vào chế biến, tỷ giá USD"),
    "VGC":  ("Industrial / Building Material", "giá nguyên liệu, tốc độ triển khai BĐS, xuất khẩu"),
    # Steel
    "HPG":  ("Steel", "giá thép HRC, giá quặng sắt, hoạt động xây dựng, xuất khẩu thép"),
    "NKG":  ("Steel", "giá thép cán nguội, biên lợi nhuận gia công, xuất khẩu"),
    # Consumer / Retail
    "MWG":  ("Consumer Electronics Retail", "sức mua tiêu dùng, tỷ lệ mở rộng cửa hàng, biên lợi nhuận"),
    "MSN":  ("Consumer / F&B", "sức mua tiêu dùng, giá nguyên liệu đầu vào, tỷ giá"),
    "VNM":  ("FMCG / Dairy", "giá sữa bột nhập khẩu, sức mua, thị phần nội địa"),
    # Energy
    "GAS":  ("Gas / Energy", "giá khí LNG, nhu cầu điện, hợp đồng Petro Vietnam"),
    "PLX":  ("Petroleum Retail", "giá dầu thô, biên lợi nhuận kinh doanh xăng dầu, tỷ giá"),
    "PVD":  ("Oil & Gas Services", "giá dầu Brent, rig day rate, capex E&P khu vực"),
    # Aviation / Logistics
    "HVN":  ("Aviation", "giá nhiên liệu jet, phục hồi du lịch quốc tế, tỷ giá USD"),
    "ACV":  ("Airport Infrastructure", "lượng hành khách, phí dịch vụ sân bay, capex mở rộng"),
    "GMD":  ("Logistics / Port", "sản lượng container, phí cảng, tăng trưởng xuất khẩu"),
    # Tech / Software
    "FPT":  ("Technology", "tăng trưởng IT outsourcing, biên lợi nhuận mảng nước ngoài, tỷ giá"),
}


def _get_sector_context(ticker: str) -> str:
    """Return a one-line sector hint for AI macro_context injection.

    Returns empty string for tickers not in the registry.
    To add a new ticker: update _SECTOR_HINTS above.
    """
    entry = _SECTOR_HINTS.get(ticker.upper())
    if not entry:
        return ""
    sector_label, key_metrics = entry
    return f"Sector: {sector_label} — metrics then chốt cần theo dõi: {key_metrics}."


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StressTestService:
    """Orchestrates stress-test for a single active thesis.

    Args:
        session:       AsyncSession for loading thesis data.
        agent:         StressTestAgent — adversarial AI caller.
        quote_service: For fetching current price of the thesis ticker.
    """

    def __init__(
        self,
        session: AsyncSession,
        agent: StressTestAgent,
        quote_service: object,
    ) -> None:
        self._session = session
        self._agent = agent
        self._quote_service = quote_service
        self._repo = ThesisRepository(session)

    async def stress_test(
        self,
        thesis_id: int,
        user_id: str,
    ) -> StressTestOutput:
        """Load thesis, build context, run adversarial stress-test.

        Args:
            thesis_id: ID of the thesis to stress-test.
            user_id:   Owner of the thesis (auth check).

        Returns:
            StressTestOutput — structured AI result, not persisted.

        Raises:
            ValueError: Thesis not found or not owned by user_id.
            PerplexityError: AI call failed.
        """
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None or str(thesis.user_id) != str(user_id):
            raise ValueError(f"Thesis {thesis_id} not found for user {user_id}")

        # Build assumptions list with IDs for agent
        assumptions = [
            {
                "id": a.id,
                "description": a.description,
                "status": str(a.status) if hasattr(a, "status") else "valid",
            }
            for a in (getattr(thesis, "assumptions", []) or [])
        ]

        # Only pass PENDING catalysts — triggered/expired ones are history
        catalysts = [
            c.description
            for c in (getattr(thesis, "catalysts", []) or [])
            if str(getattr(c, "status", "pending")).lower() == "pending"
        ]

        # Fetch current price for macro context
        current_price: float | None = None
        macro_context = ""
        try:
            quotes = await self._quote_service.get_bulk_quotes([thesis.ticker])  # type: ignore[attr-defined]
            if quotes:
                q = quotes[0]
                current_price = q.price
                macro_context = (
                    f"{thesis.ticker}: giá={q.price:,.0f} VNĐ, "
                    f"thay đổi={q.change_pct:+.2f}% hôm nay."
                )
        except Exception as exc:
            logger.warning(
                "stress_test_service.quote_failed",
                ticker=thesis.ticker,
                error=str(exc),
            )

        # Inject sector context so AI knows which metrics matter for this ticker
        sector_hint = _get_sector_context(thesis.ticker)
        if sector_hint:
            macro_context = (macro_context + "\n" + sector_hint).strip()

        logger.info(
            "stress_test_service.start",
            thesis_id=thesis_id,
            ticker=thesis.ticker,
            assumptions_count=len(assumptions),
            catalysts_count=len(catalysts),
            has_price=current_price is not None,
            has_sector_hint=bool(sector_hint),
        )

        result = await self._agent.stress_test(
            ticker=thesis.ticker,
            thesis_title=thesis.title,
            thesis_summary=getattr(thesis, "summary", "") or "",
            assumptions=assumptions,
            catalysts=catalysts,
            current_price=current_price,
            entry_price=getattr(thesis, "entry_price", None),
            target_price=getattr(thesis, "target_price", None),
            stop_loss=getattr(thesis, "stop_loss", None),
            macro_context=macro_context,
        )

        logger.info(
            "stress_test_service.complete",
            thesis_id=thesis_id,
            ticker=thesis.ticker,
            verdict=result.verdict,
            invalidation_prob=result.invalidation_probability,
        )
        return result

    async def stress_test_by_ticker(
        self,
        ticker: str,
        user_id: str,
    ) -> StressTestOutput:
        """Convenience: resolve active thesis by ticker then stress-test.

        Args:
            ticker:  Ticker symbol (case-insensitive).
            user_id: Owner of the thesis.

        Raises:
            ValueError: No active thesis found for this ticker.
        """
        theses = await self._repo.list_by_user(
            user_id=user_id,
            status="active",
        )
        matched = [t for t in theses if t.ticker.upper() == ticker.upper()]
        if not matched:
            raise ValueError(
                f"Không tìm thấy thesis active nào cho {ticker.upper()}. "
                "Hãy tạo thesis trước khi stress-test."
            )
        # If multiple active theses for same ticker, pick the most recent
        target = sorted(matched, key=lambda t: t.created_at, reverse=True)[0]
        return await self.stress_test(thesis_id=target.id, user_id=user_id)
