"""Golden tests cho src/market/indicators.py.

Fixture `indicators_golden.json` được chụp từ các hàm `_ema/_rsi/_macd_histogram/_atr`
cũ trong trend_engine.py và `_ema` (seed SMA) cũ trong rrg_service.py TRƯỚC khi gộp.
Mọi giá trị phải trùng tuyệt đối (không tolerance) — refactor không được đổi số.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.market import indicators as ind
from src.market import rrg_service, trend_engine

_GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "indicators_golden.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", sorted(_GOLDEN))
class TestGolden:
    def test_ema_seed_first_matches_trend_engine_legacy(self, case: str) -> None:
        c = _GOLDEN[case]
        assert ind.ema(c["closes"], 12) == c["ema_first_12"]
        assert trend_engine._ema(c["closes"], 12) == c["ema_first_12"]
        if "ema_first_26" in c:
            assert ind.ema(c["closes"], 26) == c["ema_first_26"]

    def test_ema_seed_sma_matches_rrg_legacy(self, case: str) -> None:
        c = _GOLDEN[case]
        assert ind.ema(c["closes"], 12, seed="sma") == c["ema_sma_12"]
        assert rrg_service._ema(c["closes"], 12) == c["ema_sma_12"]
        if "ema_sma_26" in c:
            assert ind.ema(c["closes"], 26, seed="sma") == c["ema_sma_26"]

    def test_rsi_macd_atr_match_legacy(self, case: str) -> None:
        c = _GOLDEN[case]
        assert ind.rsi(c["closes"], 14) == c["rsi_14"]
        assert ind.macd_histogram(c["closes"]) == c["macd_hist"]
        assert ind.atr(c["highs"], c["lows"], c["closes"], 14) == c["atr_14"]
        # alias trong trend_engine trỏ đúng về primitive chung
        assert trend_engine._rsi is ind.rsi
        assert trend_engine._macd_histogram is ind.macd_histogram
        assert trend_engine._atr is ind.atr


class TestEdgeCases:
    def test_ema_two_seeds_differ_only_when_series_not_flat(self) -> None:
        flat = [10.0] * 30
        assert ind.ema(flat, 5) == ind.ema(flat, 5, seed="sma")
        ramp = [float(i) for i in range(30)]
        assert ind.ema(ramp, 5)[0] == 0.0
        assert ind.ema(ramp, 5, seed="sma")[0] == 2.0

    def test_rsi_neutral_when_insufficient_and_100_when_only_gains(self) -> None:
        assert ind.rsi([1.0] * 28) == ind.RSI_NEUTRAL  # 28 < 29 bars
        assert ind.rsi([float(i) for i in range(40)]) == 100.0

    def test_sma_and_high_low(self) -> None:
        assert ind.sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5
        assert ind.sma([1.0], 2) is None
        assert ind.high_low([3.0, 9.0, 1.0, 5.0], 3) == (9.0, 1.0)
        assert ind.high_low([], 5) is None

    def test_volume_ratio(self) -> None:
        vols = [100.0] * 20 + [250.0]
        assert ind.volume_ratio(vols, baseline=20) == 2.5
        assert ind.volume_ratio([100.0] * 20, baseline=20) is None
        assert ind.volume_ratio([0.0] * 20 + [50.0], baseline=20) is None
