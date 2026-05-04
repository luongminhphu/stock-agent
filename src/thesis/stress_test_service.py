"""Stress-Test Service — orchestrate thesis stress-test flow.

Owner: thesis segment.

Responsibilities:
- Load active thesis + assumptions + catalysts from DB
- Fetch current price from market segment
- Build macro_context string for AI
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

        logger.info(
            "stress_test_service.start",
            thesis_id=thesis_id,
            ticker=thesis.ticker,
            assumptions_count=len(assumptions),
            catalysts_count=len(catalysts),
            has_price=current_price is not None,
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
