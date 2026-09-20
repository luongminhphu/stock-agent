"""Wave D4: ThesisHealthSnapshot dùng PriceSnapshot + verdict thật + rule stop chung.

Trước D4: current_price/last_verdict đọc attribute không tồn tại → distance None,
verdict luôn UNREVIEWED, assumptions_invalidated luôn 0. Test này chốt hành vi mới.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.thesis.health_snapshot import (
    AT_RISK_SCORE_THRESHOLD,
    ThesisHealthSnapshot,
    _compute_snapshot,
    _latest_verdict,
    build_thesis_health_snapshots,
)
from src.thesis.models import AssumptionStatus, ReviewVerdict, ThesisDirection, ThesisStatus
from src.thesis.price_snapshot import (
    NEAR_STOP_ATR,
    STOP_BREACHED,
    STOP_CRITICAL,
    STOP_FAR,
    STOP_NEAR,
    PriceSnapshot,
    stop_proximity,
)
from src.thesis.service import ThesisService

_NOW = datetime.now(UTC)


def _review(verdict: ReviewVerdict, days_ago: int) -> SimpleNamespace:
    return SimpleNamespace(verdict=verdict, reviewed_at=_NOW - timedelta(days=days_ago))


def _thesis(**over) -> SimpleNamespace:
    base = dict(
        id=11,
        ticker="HPG",
        title="Thép hồi phục",
        summary="HRC tăng",
        direction=ThesisDirection.BULLISH,
        status=ThesisStatus.ACTIVE,
        stop_loss=23_000.0,
        target_price=30_000.0,
        entry_price=24_000.0,
        actual_entry_price=None,
        last_reviewed_at=_NOW - timedelta(days=2),
        assumptions=[
            SimpleNamespace(status=AssumptionStatus.VALID),
            SimpleNamespace(status=AssumptionStatus.INVALID),
        ],
        reviews=[
            _review(ReviewVerdict.BULLISH, 10),
            _review(ReviewVerdict.WEAKENING, 2),
        ],
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── stop_proximity: rule chung ──────────────────────────────────────────────


def test_stop_proximity_atr_first_then_pct_fallback() -> None:
    assert stop_proximity(1.0, -0.2) == STOP_BREACHED
    assert stop_proximity(9.0, NEAR_STOP_ATR / 2) == STOP_NEAR  # ATR thắng % khi có
    assert stop_proximity(1.0, 2.5) == STOP_FAR
    assert stop_proximity(-1.0, None) == STOP_BREACHED
    assert stop_proximity(1.5, None) == STOP_CRITICAL
    assert stop_proximity(4.9, None) == STOP_NEAR
    assert stop_proximity(5.0, None) == STOP_FAR
    assert stop_proximity(None, None) is None


def test_price_snapshot_stop_helpers() -> None:
    snap = PriceSnapshot(price=23_400.0, atr14=500.0, source_quality="live")
    assert snap.stop_distance_pct(23_000.0) == pytest.approx(1.7094, rel=1e-3)
    assert snap.stop_distance_atr(23_000.0) == pytest.approx(0.8)
    assert snap.stop_proximity(23_000.0) == STOP_NEAR
    assert snap.stop_proximity(None) is None


# ── _compute_snapshot ───────────────────────────────────────────────────────


def test_snapshot_reads_real_verdict_and_invalid_assumptions() -> None:
    snap = _compute_snapshot(_thesis(), 0.7, None)
    assert snap.last_verdict == "WEAKENING"  # review mới nhất, không phải cái đầu
    assert snap.assumptions_total == 2 and snap.assumptions_invalidated == 1
    assert snap.direction == "BULLISH"
    assert snap.days_since_review == 2
    assert snap.current_price is None and snap.stop_proximity is None
    assert snap.urgency_flag == "OK"


def test_snapshot_near_stop_via_atr_is_at_risk() -> None:
    price = PriceSnapshot(price=23_400.0, atr14=500.0, source_quality="stale")
    snap = _compute_snapshot(_thesis(), 0.8, price)
    assert snap.current_price == 23_400.0
    assert snap.stop_proximity == STOP_NEAR and snap.near_stop is True
    assert snap.urgency_flag == "AT_RISK"
    assert snap.price_quality == "stale"
    text = snap.format_for_prompt()
    assert "giá=23,400 (dữ liệu cũ)" in text
    assert "stop_loss=23,000 (còn 1.7%, 0.8 ATR, SÁT STOP)" in text
    assert "giả định: 1/2 còn valid" in text and "verdict: WEAKENING" in text


def test_snapshot_far_from_stop_with_atr_stays_ok_even_if_pct_small() -> None:
    # 4% theo % nhưng 3 ATR theo ATR → FAR (không còn ngưỡng 5% riêng của health_snapshot)
    price = PriceSnapshot(price=24_000.0, atr14=320.0)
    snap = _compute_snapshot(_thesis(stop_loss=23_040.0), 0.8, price)
    assert snap.stop_proximity == STOP_FAR
    assert snap.urgency_flag == "OK"


def test_snapshot_breached_and_low_score_flags() -> None:
    price = PriceSnapshot(price=22_000.0, atr14=500.0)
    snap = _compute_snapshot(_thesis(), 0.9, price)
    assert snap.stop_breached is True and snap.urgency_flag == "AT_RISK"
    assert "(ĐÃ XUYÊN)" in snap.format_for_prompt()

    low = _compute_snapshot(_thesis(), AT_RISK_SCORE_THRESHOLD, None)
    assert low.urgency_flag == "AT_RISK"

    stale = _compute_snapshot(_thesis(last_reviewed_at=_NOW - timedelta(days=9)), 0.8, None)
    assert stale.urgency_flag == "REVIEW_DUE"

    inv = _compute_snapshot(_thesis(status=ThesisStatus.INVALIDATED), 0.8, None)
    assert inv.urgency_flag == "INVALIDATED"


def test_latest_verdict_unknown_or_missing() -> None:
    assert _latest_verdict(_thesis(reviews=[])) == "UNREVIEWED"
    assert _latest_verdict(_thesis(reviews=[SimpleNamespace(verdict="???", reviewed_at=_NOW)])) == (
        "UNREVIEWED"
    )
    assert _latest_verdict(
        _thesis(reviews=[SimpleNamespace(verdict="bearish", reviewed_at=_NOW)])
    ) == ("BEARISH")


# ── builder ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_builder_bulk_price_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.thesis.health_snapshot as mod

    monkeypatch.setattr(mod, "_fetch_score", AsyncMock(return_value=0.8))
    theses = [
        _thesis(id=1, ticker="HPG"),
        _thesis(id=2, ticker="VNM", stop_loss=60_000.0),
        _thesis(id=3, ticker="SSI", stop_loss=None, last_reviewed_at=_NOW - timedelta(days=30)),
    ]
    tcs = SimpleNamespace(
        get_many=AsyncMock(
            return_value={
                "HPG": SimpleNamespace(
                    price=23_400.0, atr14=500.0, source_quality="live", format_for_prompt=lambda: ""
                ),
                "VNM": SimpleNamespace(
                    price=70_000.0,
                    atr14=1_000.0,
                    source_quality="live",
                    format_for_prompt=lambda: "",
                ),
            }
        )
    )
    qs = SimpleNamespace(get_quote=AsyncMock(side_effect=RuntimeError("no quote")))

    out = await build_thesis_health_snapshots(
        AsyncMock(), "u1", ticker_context_service=tcs, quote_service=qs, theses=theses
    )

    tcs.get_many.assert_awaited_once_with(["HPG", "SSI", "VNM"])
    qs.get_quote.assert_awaited_once_with("SSI")  # chỉ mã thiếu mới fallback
    assert [s.ticker for s in out] == ["HPG", "SSI", "VNM"]  # AT_RISK > REVIEW_DUE > OK
    assert out[0].urgency_flag == "AT_RISK" and out[1].urgency_flag == "REVIEW_DUE"
    assert out[2].stop_proximity == STOP_FAR and out[2].current_price == 70_000.0


@pytest.mark.anyio
async def test_builder_without_price_sources_skips_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.thesis.health_snapshot as mod

    monkeypatch.setattr(mod, "_fetch_score", AsyncMock(return_value=0.8))
    loader = AsyncMock()
    monkeypatch.setattr(mod, "load_price_snapshots", loader)

    out = await build_thesis_health_snapshots(AsyncMock(), "u1", theses=[_thesis()])

    loader.assert_not_awaited()
    assert len(out) == 1 and out[0].current_price is None
    assert await build_thesis_health_snapshots(AsyncMock(), None) == []


@pytest.mark.anyio
async def test_service_get_thesis_health_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.thesis.health_snapshot as mod

    monkeypatch.setattr(mod, "_fetch_score", AsyncMock(return_value=0.42))
    svc = ThesisService.__new__(ThesisService)
    svc._session = AsyncMock()
    thesis = _thesis()
    svc._repo = SimpleNamespace(list_by_user=AsyncMock(return_value=[thesis]))
    qs = SimpleNamespace(get_quote=AsyncMock(return_value=SimpleNamespace(price=23_300.0)))

    rows = await svc.get_thesis_health("u1", quote_service=qs)

    assert len(rows) == 1
    row = rows[0]
    # keys cũ giữ nguyên
    assert row["id"] == 11 and row["ticker"] == "HPG" and row["assumption_count"] == 2
    assert row["entry_thesis"] == "HRC tăng" and row["days_since_review"] == 2
    # keys mới nuôi briefing
    assert row["status"] == "AT_RISK"  # 1.3% không ATR → CRITICAL
    assert row["stop_proximity"] == STOP_CRITICAL and row["near_stop"] is True
    assert row["health_score"] == 0.42 and row["last_verdict"] == "WEAKENING"
    assert row["current_price"] == 23_300.0 and row["price_quality"] == "quote"
    svc._repo.list_by_user.assert_awaited_once()  # không query thesis lần hai


def test_dataclass_defaults_backward_compatible() -> None:
    snap = ThesisHealthSnapshot(
        thesis_id="1",
        ticker="X",
        title="",
        direction="BULLISH",
        health_score=0.5,
        days_since_review=999,
        distance_to_stop_pct=None,
        assumptions_total=0,
        assumptions_invalidated=0,
        last_verdict="UNREVIEWED",
        urgency_flag="OK",
    )
    assert snap.near_stop is False and snap.stop_breached is False
    assert "chưa review" in snap.format_for_prompt()
