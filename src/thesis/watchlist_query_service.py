"""Deprecation shim — WatchlistQueryService has moved to watchlist segment.

This file re-exports WatchlistQueryService from its canonical location:
    src/watchlist/watchlist_query_service.py

Owner: watchlist (not thesis).

Do not add logic here. Update any remaining imports to:
    from src.watchlist.watchlist_query_service import WatchlistQueryService
"""
from src.watchlist.watchlist_query_service import WatchlistQueryService  # noqa: F401

__all__ = ["WatchlistQueryService"]
