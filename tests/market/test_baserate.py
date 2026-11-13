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
