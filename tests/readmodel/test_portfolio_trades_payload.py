"""Tests cho _build_trades_payload — composition của GET /readmodel/dashboard/portfolio/trades.

Wave 7.1: trades view lấy qty/avg_cost từ live positions (source of truth),
snapshot chỉ còn vai trò close_price fallback. Pure function — không cần DB.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.api.routes.readmodel import _build_trades_payload


def _pos(ticker: str, qty: float, avg_cost: float, thesis_id: int | None = None):
    return SimpleNamespace(ticker=ticker, qty=qty, avg_cost=avg_cost, thesis_id=thesis_id)


class TestLivePositionsAreSourceOfTruth:
    def test_live_qty_avg_override_stale_snapshot(self):
        """Snapshot cũ (46,000 @ 16,120) nhưng positions đã edit → bảng phải
        hiện giá trị live (57,500 @ 14,900). Đây là bug gốc của Wave 7.1."""
        live = [_pos("HCM", 57_500, 14_900, thesis_id=7)]
        snap_close = {"HCM": (16_150.0, "2026-08-17")}  # snapshot hôm qua

        out = _build_trades_payload(live, snap_close, price_map={}, market_open=False)

        row = out["positions"][0]
        assert row["qty"] == 57_500
        assert row["avg_cost"] == 14_900
        assert row["current_price"] == 16_150.0  # close từ snapshot
        assert row["price_stale"] is True
        assert row["snapshot_date"] == "2026-08-17"
        assert row["thesis_id"] == 7
        # P&L tính trên qty/avg LIVE × close snapshot
        assert row["unrealized_pnl"] == round((16_150 - 14_900) * 57_500, 2)

    def test_closed_position_not_resurrected_from_snapshot(self):
        """Ticker chỉ còn snapshot (đã bán hết → không còn trong positions)
        KHÔNG được xuất hiện lại trên bảng."""
        live = [_pos("VNM", 10_000, 62_300)]
        snap_close = {
            "VNM": (61_800.0, "2026-08-17"),
            "TCX": (39_000.0, "2026-08-17"),  # vị thế đã đóng
        }

        out = _build_trades_payload(live, snap_close, price_map={}, market_open=False)

        assert [p["ticker"] for p in out["positions"]] == ["VNM"]


class TestPricePriority:
    def test_realtime_price_wins_when_market_open(self):
        live = [_pos("VIC", 2_000, 105_500)]
        snap_close = {"VIC": (104_000.0, "2026-08-17")}

        out = _build_trades_payload(
            live, snap_close, price_map={"VIC": 106_000.0}, market_open=True,
        )

        row = out["positions"][0]
        assert row["current_price"] == 106_000.0
        assert row["price_stale"] is False
        assert row["unrealized_pnl"] == 1_000_000.0
        assert out["source"] == "positions_live+realtime"

    def test_offhours_last_known_is_marked_stale(self):
        """Đóng cửa: get_quote trả last_known → vẫn phải gắn badge Cuối phiên."""
        live = [_pos("VNM", 10_000, 62_300)]

        out = _build_trades_payload(
            live, snap_close={}, price_map={"VNM": 61_800.0}, market_open=False,
        )

        row = out["positions"][0]
        assert row["current_price"] == 61_800.0
        assert row["price_stale"] is True
        assert out["source"] == "positions_live"

    def test_no_price_anywhere_yields_none_not_crash(self):
        """Position mới chưa có snapshot + quote fail → price None, pct None."""
        live = [_pos("NEW", 100, 10_000)]

        out = _build_trades_payload(live, snap_close={}, price_map={}, market_open=True)

        row = out["positions"][0]
        assert row["current_price"] is None
        assert row["market_value"] is None
        assert row["unrealized_pnl"] is None
        assert row["unrealized_pct"] is None
        assert row["price_stale"] is True
        assert row["snapshot_date"] is None
        # Totals không được crash khi 1 mã thiếu giá
        assert out["total_cost_basis"] == 1_000_000.0
        assert out["total_market_value"] == 0.0


class TestTotals:
    def test_totals_aggregate_across_positions(self):
        live = [
            _pos("HCM", 57_500, 14_900),
            _pos("VIC", 2_000, 105_500),
        ]
        snap_close = {
            "HCM": (16_150.0, "2026-08-17"),
            "VIC": (106_000.0, "2026-08-17"),
        }

        out = _build_trades_payload(live, snap_close, price_map={}, market_open=False)

        expected_cost = 57_500 * 14_900 + 2_000 * 105_500
        expected_mkt = 57_500 * 16_150 + 2_000 * 106_000
        assert out["total_cost_basis"] == expected_cost
        assert out["total_market_value"] == expected_mkt
        assert out["total_unrealized_pnl"] == round(expected_mkt - expected_cost, 2)
        assert out["total_unrealized_pct"] == round(
            (expected_mkt - expected_cost) / expected_cost * 100, 4,
        )

    def test_empty_portfolio(self):
        out = _build_trades_payload([], {}, {}, market_open=False)

        assert out["positions"] == []
        assert out["total_cost_basis"] == 0.0
        assert out["total_unrealized_pct"] == 0.0
        assert out["source"] == "positions_live"
