"""UniverseQuery — tập ticker "đã biết" của user (portfolio + watchlist + thesis).

Owner: readmodel (cross-segment read projection, boundary fix B6).

market.registry_loader cần danh sách ticker trong DB để enrich SymbolRegistry nhưng
market là upstream, không được import portfolio/watchlist/thesis models. Query hợp
nhất này nằm ở readmodel; bootstrap inject callable vào registry.initialize().
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from src.platform.logging import get_logger

logger = get_logger(__name__)


async def known_tickers(session: Any) -> set[str]:
    """Distinct ticker (upper-case) từ Position, WatchlistItem, Thesis."""
    from src.portfolio.models import Position
    from src.thesis.models import Thesis
    from src.watchlist.models import WatchlistItem

    tickers: set[str] = set()
    for column in (Position.ticker, WatchlistItem.ticker, Thesis.ticker):
        rows = await session.execute(select(column).distinct())
        tickers.update(r[0].upper() for r in rows.all() if r[0])
    return tickers


def make_known_tickers_provider(
    session_factory: Callable[[], Any],
) -> Callable[[], Awaitable[set[str]]]:
    """Đóng gói session_factory thành provider không tham số cho market.registry."""

    async def _provider() -> set[str]:
        try:
            async with session_factory() as session:
                return await known_tickers(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("readmodel.universe_query.failed", error=str(exc))
            return set()

    return _provider
