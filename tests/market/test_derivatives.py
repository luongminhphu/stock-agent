"""HĐTL VN30: lịch đáo hạn, lãi/lỗ, ký quỹ, beta, phòng hộ, basis."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.market.derivatives import (
    MULTIPLIER,
    active_contracts,
    basis_stats,
    beta,
    daily_returns,
    expiry_date,
    expiry_for_code,
    hedge_plan,
    pnl,
    position_view,
    third_thursday,
)


def _all_days(d: date) -> bool:
    return d.weekday() < 5


def test_third_thursday() -> None:
    assert third_thursday(2026, 11) == date(2026, 11, 19)
    assert third_thursday(2026, 10) == date(2026, 10, 15)


def test_expiry_moves_back_on_holiday() -> None:
    holiday = date(2026, 11, 19)
    d = expiry_date(2026, 11, lambda x: x.weekday() < 5 and x != holiday)
    assert d == date(2026, 11, 18)


def test_active_contracts_roll_after_expiry() -> None:
    before = active_contracts(date(2026, 11, 19), _all_days)
    assert [c.code for c in before] == ["VN30F2611", "VN30F2612", "VN30F2703", "VN30F2706"]
    after = active_contracts(date(2026, 11, 20), _all_days)
    assert [c.code for c in after] == ["VN30F2612", "VN30F2701", "VN30F2703", "VN30F2706"]
    assert after[0].expiry == date(2026, 12, 17)


def test_pnl_and_position_view() -> None:
    assert pnl("long", 2, 1300.0, 1310.5) == pytest.approx(10.5 * MULTIPLIER * 2)
    assert pnl("SHORT", 1, 1300.0, 1310.0) == pytest.approx(-10 * MULTIPLIER)
    v = position_view(
        "vn30f2611", "SHORT", 3, 1300.0, 1290.0, date(2026, 11, 16), is_trading_day=_all_days
    )
    assert v.unrealized_pnl == 10 * MULTIPLIER * 3
    assert v.expiry == date(2026, 11, 19) and v.days_to_expiry == 3 and v.roll_warning
    assert v.initial_margin == round(1300 * MULTIPLIER * 3 * 0.17)
    with pytest.raises(ValueError):
        pnl("BUY", 1, 1, 2)


def test_expiry_for_series_alias_is_unknown() -> None:
    assert expiry_for_code("VN30F1M") is None
    assert expiry_for_code("VN30F2612", _all_days) == date(2026, 12, 17)


def test_beta_of_scaled_series() -> None:
    idx = [1000 * (1 + 0.01 * ((i % 7) - 3)) for i in range(60)]
    ri = daily_returns(idx)
    assert beta([2 * r for r in ri], ri) == pytest.approx(2.0)
    assert beta(ri[:5], ri[:5]) is None


def test_hedge_plan_contract_count() -> None:
    # Danh mục 2 tỷ, beta 1.2 → phơi nhiễm 2,4 tỷ; 1 HĐ ở 1.200 điểm = 120 triệu → 20 HĐ
    plan = hedge_plan(
        [("FPT", 1_000_000_000, 1.4), ("VNM", 1_000_000_000, 1.0)],
        futures_price=1200.0,
        contract_code="VN30F2611",
        index_level=1195.0,
    )
    assert plan.portfolio_beta == pytest.approx(1.2)
    assert plan.contracts_to_short == 20
    assert plan.basis_points == 5.0 and plan.basis_cost == 5 * MULTIPLIER * 20
    half = hedge_plan([("FPT", 2_000_000_000, None)], 1200.0, "VN30F2611", hedge_ratio=0.5)
    assert half.missing_beta == ["FPT"] and half.contracts_to_short == 8  # beta giả định 1


def test_basis_stats_groups_by_days_to_expiry() -> None:
    exp = [date(2026, 10, 15), date(2026, 11, 19)]
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(45)]
    fut = {
        d: 1200 + (exp[0] - d).days * 0.5 if d <= exp[0] else 1200 + (exp[1] - d).days * 0.5
        for d in days
    }
    idx = dict.fromkeys(days, 1200.0)
    st = basis_stats(fut, idx, exp, current=8.0)
    assert st.samples == 45
    assert st.by_days_to_expiry["0-3"] < st.by_days_to_expiry[">20"]
    assert 0 <= st.percentile_of_current <= 100


def test_krx_code_roundtrip() -> None:
    from src.market.derivatives import krx_code

    assert krx_code(2025, 6) == "41I1F6000"
    assert krx_code(2026, 11) == "41I1GB000"
    assert krx_code(2019, 12) == "41I19C000"
    assert expiry_for_code("41I1GB000", _all_days) == date(2026, 11, 19)
    c = active_contracts(date(2026, 11, 2), _all_days)[0]
    assert (c.code, c.krx) == ("VN30F2611", "41I1GB000")


class _Bar:
    def __init__(self, d: date, c: float) -> None:
        self.date, self.close, self.open, self.high, self.low, self.volume = d, c, c, c, c, 1000


class _FakeOHLCV:
    def __init__(self, series: dict[str, list[_Bar]]) -> None:
        self.series = series

    async def get_candles(self, sym, start, end):  # type: ignore[no-untyped-def]
        return [b for b in self.series.get(sym, []) if start <= b.date <= end]


class _FakeBoard:
    async def fetch_derivative_quotes(self, codes):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        from src.market.adapters.mock import _make_mock_quote

        return [replace(_make_mock_quote(c), ticker=c, price=1305.0) for c in codes[:1]]


@pytest.mark.anyio
async def test_service_contracts_basis_and_beta(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.market import ohlcv_service
    from src.market.derivatives import DerivativesService

    today = date(2026, 11, 10)
    monkeypatch.setattr(ohlcv_service, "_today_ict", lambda: today)
    days = [today - timedelta(days=i) for i in range(300, -1, -1)]
    vn30 = [_Bar(d, 1300 + (i % 5)) for i, d in enumerate(days)]
    fpt = [_Bar(d, 100 * (1 + 0.02 * ((i % 5) - 2))) for i, d in enumerate(days)]
    fut = [_Bar(d, b.close + 3) for d, b in zip(days, vn30, strict=True)]
    svc = DerivativesService(_FakeOHLCV({"VN30": vn30, "FPT": fpt, "VN30F1M": fut}), _FakeBoard())

    snap = await svc.contracts(today)
    f1 = snap["contracts"][0]
    assert f1["series"] == "F1M" and f1["price"] == 1305.0
    assert f1["basis"] == round(1305.0 - vn30[-1].close, 1)

    st = await svc.basis_history(today, days=200)
    assert st.samples > 100 and st.median == pytest.approx(3.0)

    betas = await svc.betas(["FPT", "NONE"])
    assert betas["FPT"] is not None and betas["NONE"] is None


@pytest.fixture(autouse=True)
def _reset_quote_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.market import derivatives

    monkeypatch.setattr(derivatives, "_quotes_backoff_until", 0.0)


class _BrokenBoard:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch_derivative_quotes(self, codes):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise RuntimeError("400 Bad Request")


@pytest.mark.anyio
async def test_contracts_fallback_to_continuous_and_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.market import ohlcv_service
    from src.market.derivatives import DerivativesService

    today = date(2026, 11, 13)
    monkeypatch.setattr(ohlcv_service, "_today_ict", lambda: today)
    days = [today - timedelta(days=i) for i in range(20, -1, -1)]
    vn30 = [_Bar(d, 1380.0) for d in days]
    fut = [_Bar(d, 1376.5) for d in days]
    board = _BrokenBoard()
    svc = DerivativesService(_FakeOHLCV({"VN30": vn30, "VN30F1M": fut}), board)

    snap = await svc.contracts(today)
    rows = {r["series"]: r for r in snap["contracts"]}
    assert rows["F1M"]["price"] == 1376.5 and rows["F1M"]["price_source"] == "continuous"
    assert rows["F1M"]["basis"] == -3.5
    assert rows["F2M"]["price"] is None and rows["F2M"]["price_source"] is None
    # Nguồn lỗi → tạm ngưng gọi lại (không gọi dồn mỗi request)
    await svc.contracts(today)
    assert board.calls == 1


@pytest.mark.anyio
async def test_board_price_preferred_over_continuous(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.market import ohlcv_service
    from src.market.derivatives import DerivativesService

    today = date(2026, 11, 13)
    monkeypatch.setattr(ohlcv_service, "_today_ict", lambda: today)
    days = [today - timedelta(days=i) for i in range(20, -1, -1)]
    bars = [_Bar(d, 1380.0) for d in days]
    svc = DerivativesService(_FakeOHLCV({"VN30": bars, "VN30F1M": bars}), _FakeBoard())
    f1 = (await svc.contracts(today))["contracts"][0]
    assert f1["price"] == 1305.0 and f1["price_source"] == "board"


@pytest.mark.anyio
async def test_vci_adapter_parses_derivative_board(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.market.adapters.vci import VCIAdapter

    sent: list[list[str]] = []

    async def fake_board(self, symbols):  # type: ignore[no-untyped-def]
        sent.append(symbols)
        return [
            {
                "listingInfo": {
                    "symbol": "41I1GB000",
                    "refPrice": 1375.0,
                    "ceiling": 1471.2,
                    "floor": 1278.8,
                },
                "matchPrice": {
                    "matchPrice": 1381.4,
                    "matchVolume": 120000,
                    "highest": 1385,
                    "lowest": 1370,
                },
            },
            {"listingInfo": {"symbol": "BAD"}, "matchPrice": {}},  # thiếu refPrice → bỏ
            {"listingInfo": None, "matchPrice": {"matchPrice": 0}},  # mã VCI không nhận → bỏ
        ]

    monkeypatch.setattr(VCIAdapter, "_fetch_price_board", fake_board)
    adapter = VCIAdapter()
    try:
        qs = await adapter.fetch_derivative_quotes(["41i1gb000", "vn30f2611"])
    finally:
        await adapter.close()
    assert sent == [["41I1GB000", "VN30F2611"]]
    assert [(q.ticker, q.price) for q in qs] == [("41I1GB000", 1381.4)]
    assert await VCIAdapter().fetch_derivative_quotes([]) == []
