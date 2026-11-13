"""Base-rate engine: trạng thái point-in-time, đợt độc lập, co về mức nền, gợi ý."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from src.market.baserate import (
    MIN_EPISODES,
    SUGGEST_INSUFFICIENT,
    SUGGEST_LEAN_AVOID,
    SUGGEST_LEAN_BUY,
    FundamentalPoint,
    _pick_episodes,
    _posterior,
    analyze,
    compute_states,
)


@dataclass(frozen=True)
class _Bar:
    date: date
    close: float
    low: float
    volume: int


def _series(n: int, period: int = 260, amp: float = 0.35, drift: float = 0.0) -> list[_Bar]:
    """Giá chạy theo chu kỳ hình sin — quy luật biết trước để kiểm chứng engine."""
    start = date(2014, 1, 1)
    bars = []
    for i in range(n):
        c = 30_000 * (1 + amp * math.sin(2 * math.pi * i / period)) * (1 + drift) ** i
        bars.append(_Bar(start + timedelta(days=i), c, c * 0.99, 1_000_000))
    return bars


def test_insufficient_history() -> None:
    rep = analyze("abc", _series(200))
    assert rep.suggestion == SUGGEST_INSUFFICIENT
    assert rep.confidence == "khong_du" and rep.ticker == "ABC"


def test_states_only_use_published_fundamentals() -> None:
    bars = _series(400)
    pub = bars[300].date
    funds = [FundamentalPoint(pub, pe=8.0, eps_growth_yoy=0.3)]
    states = compute_states(bars, funds)
    assert "earnings" not in (states[299] or {}), "chưa công bố thì không được dùng"
    assert states[300]["earnings"] == "strong"


def test_pe_percentile_is_point_in_time() -> None:
    bars = _series(600)
    funds = [FundamentalPoint(bars[260 + 40 * k].date, pe=10.0 + k) for k in range(8)]
    states = compute_states(bars, funds)
    # P/E mới nhất luôn cao nhất so với các bản đã công bố trước đó → "expensive"
    assert states[-1]["valuation"] == "expensive"


def test_episodes_do_not_overlap() -> None:
    assert _pick_episodes([1, 2, 3, 130, 131, 260], 120) == [1, 130, 260]


def test_posterior_shrinks_small_samples_to_base() -> None:
    small, _, _ = _posterior(3, 3, 0.5)
    large, _, _ = _posterior(30, 30, 0.5)
    assert 0.5 < small < large < 1.0


def test_cycle_bottom_leans_buy_and_top_leans_avoid() -> None:
    bars = _series(260 * 10 + 196)  # phiên cuối i=2795 ≡ 195 (3/4 chu kỳ) → đáy sin
    rep = analyze("CYC", bars)
    out = rep.outcomes[120]
    assert out.episodes >= MIN_EPISODES
    assert rep.suggestion == SUGGEST_LEAN_BUY, rep
    assert out.edge > 0 and out.median_return > 0
    assert all(a.entry_date < rep.as_of for a in rep.analogs)

    top = analyze("CYC", _series(260 * 10 + 66))  # phiên cuối i=2665 ≡ 65 (1/4 chu kỳ) → đỉnh sin
    assert top.suggestion == SUGGEST_LEAN_AVOID, top


def test_relaxes_conditions_and_reports_dropped() -> None:
    bars = _series(260 * 4 + 65)
    funds = [
        FundamentalPoint(bars[i].date, pe=10 + (i % 7), eps_growth_yoy=0.1)
        for i in range(0, len(bars), 63)
    ]
    rep = analyze("CYC", bars, funds)
    assert rep.matched_on, "luôn còn ít nhất 1 điều kiện"
    assert set(rep.matched_on) | set(rep.dropped) <= set(rep.state)
    if rep.dropped:
        assert any("bỏ điều kiện" in n for n in rep.notes)


# ── Service: chỉ dùng phiên đã đóng cửa + một báo cáo cho mọi nơi ────────────


class _TodayOHLCV:
    """Chuỗi nến kết thúc HÔM NAY (nến cuối = phiên đang giao dịch nếu chưa chốt)."""

    def __init__(self, today: date, last_close: float) -> None:
        bars = _series(700)
        shift = today - bars[-1].date
        self.bars = [_Bar(b.date + shift, b.close, b.low, b.volume) for b in bars]
        last = self.bars[-1]
        self.bars[-1] = _Bar(last.date, last_close, last_close * 0.99, last.volume)
        self.calls = 0

    async def get_candles(self, ticker, from_date, to_date):  # type: ignore[no-untyped-def]
        self.calls += 1
        return list(self.bars)


def _run(coro):  # type: ignore[no-untyped-def]
    import asyncio

    return asyncio.run(coro)


def test_service_ignores_unclosed_session_and_caches(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import src.market.baserate as br
    from src.market.ohlcv_service import _today_ict

    br._REPORT_CACHE.clear()
    today = _today_ict()
    src = _TodayOHLCV(today, last_close=99_999.0)
    svc = br.BaseRateService(src)

    # Trong phiên: nến hôm nay (giá đang chạy) bị bỏ → báo cáo tính đến phiên trước
    monkeypatch.setattr(br, "_session_closed", lambda t: False)
    r1 = _run(svc.analyze("hpg"))
    assert r1.as_of < today and r1.last_close != 99_999.0

    # Giá trong phiên đổi → kết quả KHÔNG đổi (bảng và drawer luôn khớp nhau)
    src.bars[-1] = _Bar(today, 12_345.0, 12_000.0, 1)
    r2 = _run(svc.analyze("HPG"))
    assert r2 is r1

    # Sau giờ chốt giá: dùng cả phiên hôm nay
    monkeypatch.setattr(br, "_session_closed", lambda t: True)
    r3 = _run(svc.analyze("HPG"))
    assert r3.as_of == today and r3.last_close == 12_345.0
    br._REPORT_CACHE.clear()


def test_main_horizon_and_cost_drive_suggestion() -> None:
    """Tầm quyết định gợi ý + chi phí khứ hồi cấu hình được (HĐ tương lai dùng 20 phiên)."""
    from src.market.baserate import _suggest

    bars = _series(260 * 6, period=90)
    default = analyze("X", bars, horizons=(5, 20, 60))
    short = analyze("X", bars, horizons=(5, 20, 60), main_horizon=20, cost=0.0003)
    assert default.suggestion == _suggest(default.outcomes[60])
    assert short.suggestion == _suggest(short.outcomes[20], 0.0003)
    # Chi phí thấp hơn → mức nền "thắng" không thấp hơn
    assert short.outcomes[20].base_win_rate >= default.outcomes[20].base_win_rate
    # Tầm không có trong danh sách → về mặc định (tầm dài nhất)
    assert analyze("X", bars, horizons=(5, 60), main_horizon=20).suggestion == default.suggestion


def test_market_regime_feature_point_in_time() -> None:
    from src.market.baserate import market_regimes

    up = [_Bar(date(2014, 1, 1) + timedelta(days=i), 1000 + i, 990 + i, 1) for i in range(300)]
    down = [
        _Bar(up[-1].date + timedelta(days=i + 1), 1300 - 3 * i, 1290 - 3 * i, 1) for i in range(300)
    ]
    regs = dict(market_regimes(up + down))
    assert regs[up[-1].date] == "bull"
    assert regs[down[-1].date] == "bear"
    assert up[100].date not in regs  # chưa đủ MA200

    stock = _series(600)
    states = compute_states(stock, market_bars=up + down)
    last = states[-1]
    assert last is not None and last["market"] == regs[stock[-1].date]
    # Ngày cổ phiếu không có phiên VN-Index trong 7 ngày → không gán bối cảnh
    assert "market" not in (compute_states(stock, market_bars=up[:260])[-1] or {})


def test_analyze_matches_on_market_when_available() -> None:
    stock = _series(260 * 6, period=90)
    market = [_Bar(b.date, 1000 + (i % 400), 990, 1) for i, b in enumerate(stock)]
    rep = analyze("X", stock, market_bars=market)
    assert "market" in rep.state
    assert "market" in rep.matched_on or "market" in rep.dropped
    assert "market" not in analyze("X", stock).state


@dataclass(frozen=True)
class _HLBar:
    date: date
    close: float
    low: float
    high: float
    volume: int = 1


def test_path_stats_target_vs_stop_first() -> None:
    from src.market.baserate import path_stats

    d0 = date(2020, 1, 1)

    def mk(closes: list[float]) -> list[_HLBar]:
        return [_HLBar(d0 + timedelta(days=i), c, c * 0.99, c * 1.01) for i, c in enumerate(closes)]

    up = mk([100, 102, 104, 106, 108, 111, 112])  # +10% ở phiên 5 (high 112,1)
    down = mk([100, 97, 93, 90, 88, 87, 86])  # −10% ở phiên 3 (low 89,1)
    flat = mk([100, 101, 100, 101, 100, 101, 100])
    bars = up + down + flat
    ps = path_stats(bars, [0, 7, 14], stop_pct=0.10, target_pct=0.10, horizon=6)
    assert ps.episodes == 3
    assert (ps.target_first, ps.stop_first, ps.neither) == (0.3333, 0.3333, 0.3333)
    assert ps.median_days_to_target == 5 and ps.median_days_to_stop == 3
    # Không có Target → chỉ thống kê Stop-loss
    assert path_stats(bars, [0, 7], 0.10, None, 6).target_first == 0.0
    assert path_stats(bars, [20], 0.1, 0.1, 6).episodes == 0  # thiếu dữ liệu tương lai


def test_analyze_reports_path_with_default_or_custom_levels() -> None:
    from src.market.baserate import default_levels

    bars = _series(260 * 6, period=90)
    rep = analyze("X", bars)
    assert rep.path is not None and rep.path.episodes > 0
    main = rep.outcomes[max(rep.outcomes)]
    assert (rep.path.stop_pct, rep.path.target_pct) == default_levels(main)
    custom = analyze("X", bars, stop_pct=0.05, target_pct=0.2)
    assert (custom.path.stop_pct, custom.path.target_pct) == (0.05, 0.2)
    total = custom.path.target_first + custom.path.stop_first + custom.path.neither
    assert abs(total - 1) < 1e-3


def test_track_record_walk_forward_no_lookahead() -> None:
    from src.market.baserate import RELIABLE, UNPROVEN, WEAK, track_record

    bars = _series(260 * 8, period=90)
    rec = track_record("X", bars, step=20)
    assert rec.checked > 0 and rec.horizon == 120
    assert rec.verdict in {RELIABLE, WEAK, UNPROVEN}
    assert sum(r.count for r in rec.signals.values()) == rec.checked
    # Lần kiểm cuối phải còn đủ `horizon` phiên tương lai để đo kết quả
    assert rec.period_to is not None
    assert rec.period_to <= bars[-1 - rec.horizon].date
    for b in rec.calibration:
        assert 0 <= b.predicted <= 1 and 0 <= b.realized <= 1 and b.count > 0
    # Chu kỳ biết trước (sin) → engine tiền lệ phải bắt được ít nhất một chiều
    assert rec.signals["NGHIENG_MUA"].count + rec.signals["NGHIENG_TRANH"].count > 0


def test_track_record_too_short_history() -> None:
    from src.market.baserate import UNPROVEN, track_record

    rec = track_record("X", _series(400))
    assert rec.checked == 0 and rec.verdict == UNPROVEN and rec.base_win_rate is None
