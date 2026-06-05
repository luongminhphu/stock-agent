"""readmodel segment — optimised read queries for UI/API.

Public surface:
    DashboardService        — facade, delegates to sub-services
    StatsService            — KPI overview
    ThesisQueryService      — thesis list, detail, catalysts
    PortfolioQueryService   — thesis-based portfolio view
    BacktestingService      — verdict accuracy, thesis performance, price snapshots
    LeaderboardService      — leaderboard queries
    ThesisTimelineService   — thesis timeline
    DashboardTTLCache       — in-process TTL cache (Wave 2B)
    CacheSubscriber         — event-bus cache invalidation hooks (Wave 3)
    get_readmodel_cache     — returns the shared DashboardTTLCache instance
    RecentReviewsStore      — cross-thesis recent AI review surface (Wave 1)
    IntelligenceSnapshotStore      — per-user cache for IntelligenceReport (Wave D)
    IntelligenceSnapshotSubscriber — event-bus wiring: auto-upsert on engine cycle (Gap 2)
    get_intelligence_snapshot      — returns the process-level snapshot store singleton
    TodayLoopQueryService   — aggregate today's signals into actionable view (Gap 5)

Startup wiring (call once in lifespan / startup hook)::

    from src.readmodel import CacheSubscriber, IntelligenceSnapshotSubscriber
    CacheSubscriber.register()
    IntelligenceSnapshotSubscriber.register()
"""

from src.readmodel.backtesting_service import BacktestingService
from src.readmodel.cache import DashboardTTLCache
from src.readmodel.cache_subscriber import CacheSubscriber, get_cache as get_readmodel_cache
from src.readmodel.dashboard_service import DashboardService
from src.readmodel.intelligence_snapshot import (
    IntelligenceSnapshotStore,
    IntelligenceSnapshotSubscriber,
    get_intelligence_snapshot,
)
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.portfolio_query_service import PortfolioQueryService
from src.readmodel.recent_reviews_store import RecentReviewsStore
from src.readmodel.stats_service import StatsService
from src.readmodel.thesis_query_service import ThesisQueryService
from src.readmodel.timeline_service import ThesisTimelineService
from src.readmodel.today_loop_query_service import TodayLoopQueryService

__all__ = [
    "DashboardService",
    "StatsService",
    "ThesisQueryService",
    "PortfolioQueryService",
    "BacktestingService",
    "LeaderboardService",
    "ThesisTimelineService",
    "DashboardTTLCache",
    "CacheSubscriber",
    "get_readmodel_cache",
    "RecentReviewsStore",
    "IntelligenceSnapshotStore",
    "IntelligenceSnapshotSubscriber",
    "get_intelligence_snapshot",
    "TodayLoopQueryService",
]
