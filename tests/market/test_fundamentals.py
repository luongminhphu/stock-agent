"""market.fundamentals — đọc chỉ số VCI thành dữ liệu cơ bản point-in-time cho base rate."""

from __future__ import annotations

import asyncio
from datetime import date

from src.market.baserate import analyze
from src.market.fundamentals import PUBLISH_LAG_DAYS, VciFundamentals, parse_statistics
from tests.market.test_baserate import _series


def _row(y: int, q: int, pe: float | None, cap: float, shares: float = 10.0) -> dict:
    return {"year": y, "quarter": q, "pe": pe, "marketCap": cap, "numberOfSharesMktCap": shares}


def test_parse_eps_growth_and_publication_lag() -> None:
    rows = [
        _row(2024, 1, 10.0, 1000.0),  # EPS = 1000/10/10 = 10
        _row(2025, 1, 8.0, 1200.0),  # EPS = 1200/10/8 = 15 → +50% so với Q1/2024
        _row(2025, 5, 9.0, 1300.0),  # dòng năm → bỏ
        {"year": None, "quarter": 2, "pe": 5},  # thiếu kỳ → bỏ
    ]
    pts = parse_statistics(rows)
    assert [p.published_on for p in pts] == [date(2024, 5, 15), date(2025, 5, 15)]
    assert PUBLISH_LAG_DAYS == 45
    assert pts[0].pe == 10.0 and pts[0].eps_growth_yoy is None  # chưa có cùng kỳ năm trước
    assert pts[1].pe == 8.0 and pts[1].eps_growth_yoy == 0.5


def test_parse_loss_making_quarter_has_no_growth() -> None:
    pts = parse_statistics([_row(2024, 2, 12.0, 1200.0), _row(2025, 2, -4.0, 800.0)])
    assert pts[1].pe == -4.0 and pts[1].eps_growth_yoy is None
    assert parse_statistics({"not": "a list"}) == []
    # Hỗ trợ tên cột yearReport/lengthReport
    alt = parse_statistics([{"yearReport": 2024, "lengthReport": 3, "pe": 7.5}])
    assert alt and alt[0].published_on == date(2024, 11, 14)


def test_analyze_uses_fundamentals_when_available() -> None:
    bars = _series(260 * 6)
    start = bars[0].date
    rows = []
    for k in range(24):  # 6 năm số liệu quý, lợi nhuận tăng đều 10%/năm
        y, q = start.year + k // 4, k % 4 + 1
        rows.append(_row(y, q, 10.0 + (k % 5), 1000.0 * (1.1 ** (k // 4)) * (10.0 + (k % 5)) / 10))
    pts = parse_statistics(rows)
    rep = analyze("HPG", bars, pts)
    assert not any("Chưa có dữ liệu cơ bản" in n for n in rep.notes)
    assert "earnings" in rep.state or "valuation" in rep.state


def test_provider_caches_per_ticker(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    prov = VciFundamentals()
    calls: list[str] = []

    async def fake_fetch(t: str):  # type: ignore[no-untyped-def]
        calls.append(t)
        return parse_statistics([_row(2024, 1, 10.0, 1000.0)])

    monkeypatch.setattr(prov, "_fetch", fake_fetch)
    a = asyncio.run(prov.get_fundamentals("hpg"))
    b = asyncio.run(prov.get_fundamentals("HPG"))
    assert a == b and calls == ["HPG"]
