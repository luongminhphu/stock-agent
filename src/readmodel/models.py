"""readmodel.models — compat re-export (Wave E1).

readmodel là projection/query (CQRS read side), không sở hữu bảng. Các ORM trước đây
định nghĩa ở đây đã trả về đúng owner; tên bảng giữ nguyên → không cần migration:

    market_quote_cache, trend_snapshots, trend_predictions  → src/market/models.py   (E1a)
    intelligence_snapshots, global_risk_snapshots           → src/core/models.py     (E1b)
    daily_agendas                                           → src/briefing/models.py (E1c)

Re-export bên dưới giữ tương thích 1 wave; consumer mới import trực tiếp từ owner.
Persist qua ``platform.db.upsert_rows``.
"""

from __future__ import annotations

from src.briefing.models import DailyAgenda
from src.core.models import GlobalRiskSnapshot, IntelligenceSnapshot
from src.market.models import MarketQuoteCache, TrendPrediction, TrendSnapshot

__all__ = [
    "DailyAgenda",
    "GlobalRiskSnapshot",
    "IntelligenceSnapshot",
    "MarketQuoteCache",
    "TrendPrediction",
    "TrendSnapshot",
]
