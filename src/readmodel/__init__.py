"""readmodel segment — optimised read queries and projections for UI/API.

Owner: readmodel segment.
Exports the three public services. All are read-only; no writes here.
"""

from src.readmodel.dashboard_service import DashboardService
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.timeline_service import ThesisTimelineService

__all__ = ["DashboardService", "LeaderboardService", "ThesisTimelineService"]
