"""readmodel segment — optimised read queries for UI/API.

Public surface:
    DashboardService        — facade, delegates to sub-services
    StatsService            — KPI overview
    ThesisQueryService      — thesis list, detail, catalysts
    PortfolioQueryService   — thesis-based portfolio view
    BacktestingService      — verdict accuracy, thesis performance, price snapshots
    LeaderboardService      — leaderboard queries
    ThesisTimelineService   — thesis timeline
"""

from src.readmodel.backtesting_service import BacktestingService
from src.readmodel.dashboard_service import DashboardService
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.portfolio_query_service import PortfolioQueryService
from src.readmodel.stats_service import StatsService
from src.readmodel.thesis_query_service import ThesisQueryService
from src.readmodel.timeline_service import ThesisTimelineService

__all__ = [
    "DashboardService",
    "StatsService",
    "ThesisQueryService",
    "PortfolioQueryService",
    "BacktestingService",
    "LeaderboardService",
    "ThesisTimelineService",
]
