"""Compat shim (Wave E1a): TrendSnapshotStore đã chuyển sang ``src.market.trend_snapshot_store``.

Xoá ở wave sau khi mọi consumer import trực tiếp từ market.
"""

from src.market.trend_snapshot_store import (  # noqa: F401
    TrendSnapshotStore,
    load_snapshots_from_db,
)

__all__ = ["TrendSnapshotStore", "load_snapshots_from_db"]
