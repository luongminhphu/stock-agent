"""DashboardService — facade cho readmodel segment.

Owner: readmodel segment.

Endpoints served (via src/api/routes/readmodel.py):
    get_stats()                  — delegates to StatsService
    get_theses_list()            — delegates to ThesisQueryService
    get_thesis_detail()          — delegates to ThesisQueryService
    get_upcoming_catalysts()     — delegates to ThesisQueryService
    get_scan_latest()            — snapshot scan gan nhat (WatchlistScan)
    get_brief_latest()           — snapshot brief gan nhat (BriefSnapshot)
    get_verdict_accuracy()       — delegates to BacktestingService
    get_thesis_performances()    — delegates to BacktestingService
    get_price_snapshots()        — delegates to BacktestingService
    get_portfolio()              — delegates to PortfolioQueryService

Design rules:
- This class is a thin facade — no query logic lives here.
- SELECT-only: no writes, no business logic, no AI calls.
- All public methods are async and accept an AsyncSession (via constructor).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.readmodel.backtesting_service import BacktestingService
from src.readmodel.portfolio_query_service import PortfolioQueryService
from src.readmodel.stats_service import StatsService
from src.readmodel.thesis_query_service import ThesisQueryService

logger = get_logger(__name__)


class QuoteBatchReader(Protocol):
    """Minimal batch-quote interface required by DashboardService.

    Any object with a compatible get_quotes() method satisfies this contract.
    Keeps DashboardService loosely coupled from the market segment.
    """

    async def get_quotes(self, tickers: list[str]): ...  # noqa: D102


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stats = StatsService(session)
        self._thesis_query = ThesisQueryService(session)
        self._portfolio_query = PortfolioQueryService(session)
        self._backtesting = BacktestingService(session)

    # ------------------------------------------------------------------
    # 1. Stats — delegates to StatsService
    # ------------------------------------------------------------------

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        return await self._stats.get_stats(user_id)

    # ------------------------------------------------------------------
    # 2-4. Thesis queries — delegates to ThesisQueryService
    # ------------------------------------------------------------------

    async def get_theses_list(
        self,
        user_id: str,
        status: str | None = "active",
        ticker: str | None = None,
        limit: int = 200,
        price_map: dict[str, float] | None = None,
        position_map: dict[str, tuple[float, float]] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._thesis_query.get_theses_list(
            user_id=user_id,
            status=status,
            ticker=ticker,
            limit=limit,
            price_map=price_map,
            position_map=position_map,
        )

    async def get_thesis_detail(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        return await self._thesis_query.get_thesis_detail(user_id, thesis_id)

    async def get_upcoming_catalysts(self, user_id: str, days: int = 30) -> list[dict[str, Any]]:
        return await self._thesis_query.get_upcoming_catalysts(user_id, days=days)

    # ------------------------------------------------------------------
    # 5. Latest scan snapshot — cross-segment (watchlist)
    # ------------------------------------------------------------------

    async def get_scan_latest(self, user_id: str) -> dict[str, Any] | None:
        try:
            from src.watchlist.models import WatchlistScan
        except ImportError:
            logger.warning(
                "get_scan_latest.import_error", detail="WatchlistScan model not available"
            )
            return None

        try:
            row = (
                await self._session.execute(
                    select(WatchlistScan)
                    .where(WatchlistScan.user_id == user_id)
                    .order_by(WatchlistScan.scanned_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if not row:
                return None

            return {
                "id": row.id,
                "user_id": row.user_id,
                "summary": row.summary,
                "scanned_at": row.scanned_at.isoformat() if row.scanned_at else None,
            }
        except Exception as exc:
            logger.warning("get_scan_latest.db_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # 6. Latest brief snapshot — cross-segment (briefing)
    # ------------------------------------------------------------------

    async def get_brief_latest(self, user_id: str, phase: str = "morning") -> dict[str, Any] | None:
        try:
            from src.briefing.models import BriefSnapshot
        except ImportError:
            logger.warning(
                "get_brief_latest.import_error", detail="BriefSnapshot model not available"
            )
            return None

        try:
            row = (
                await self._session.execute(
                    select(BriefSnapshot)
                    .where(
                        BriefSnapshot.user_id == user_id,
                        BriefSnapshot.phase == phase,
                    )
                    .order_by(BriefSnapshot.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if not row:
                return None

            try:
                parsed_content = json.loads(row.content)
            except (json.JSONDecodeError, TypeError):
                parsed_content = {"summary": row.content, "content": row.content}

            return {
                "id": row.id,
                "user_id": row.user_id,
                "phase": row.phase,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                **parsed_content,
            }
        except Exception as exc:
            logger.warning("get_brief_latest.db_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # 7-9. Backtesting — delegates to BacktestingService
    # ------------------------------------------------------------------

    async def get_verdict_accuracy(self, user_id: str) -> list[dict[str, Any]]:
        return await self._backtesting.get_verdict_accuracy(user_id)

    async def get_thesis_performances(
        self,
        user_id: str,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._backtesting.get_thesis_performances(user_id, ticker=ticker, limit=limit)

    async def get_price_snapshots(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        return await self._backtesting.get_price_snapshots(user_id, thesis_id)

    # ------------------------------------------------------------------
    # 10. Portfolio — delegates to PortfolioQueryService
    # ------------------------------------------------------------------

    async def get_portfolio(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
        quote_service: QuoteBatchReader | None = None,
    ) -> dict[str, Any]:
        """Return portfolio data for user, optionally enriched with live prices.

        Price resolution order:
          1. price_map if provided (explicit caller override)
          2. quote_service.get_quotes() if provided (auto-fetch)
          3. No prices (portfolio returned without current price data)
        """
        if price_map is None and quote_service is not None:
            theses = await self._thesis_query.get_theses_list(
                user_id=user_id, status="active", limit=500
            )
            tickers = list({t["ticker"] for t in theses if t.get("ticker")})
            if tickers:
                try:
                    quotes = await quote_service.get_quotes(tickers)
                    price_map = {q.ticker: q.close for q in quotes if q.close is not None}
                except Exception as exc:
                    logger.warning(
                        "dashboard_service.get_portfolio.price_fetch_failed",
                        error=str(exc),
                    )
                    price_map = {}

        return await self._portfolio_query.get_portfolio(user_id, price_map=price_map)
