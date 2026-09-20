"""Compat shim (Wave E1a): TrendPredictionStore đã chuyển sang ``src.market.trend_prediction_store``.

Xoá ở wave sau khi mọi consumer import trực tiếp từ market.
"""

from src.market.trend_prediction_store import (  # noqa: F401
    TrendPredictionStore,
    load_predictions_from_db,
)

__all__ = ["TrendPredictionStore", "load_predictions_from_db"]
