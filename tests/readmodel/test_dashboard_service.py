"""Integration tests for readmodel.DashboardService thesis read path.

Uses in-memory SQLite via session fixture (tests/conftest.py).
No HTTP calls, no AI calls (review agent is mocked).

Chỉ test các query chạy được trên SQLite: ``get_theses_list`` /
``get_thesis_detail``. ``get_stats`` dùng ``func.timezone`` (Postgres-only)
nên không test ở đây.
"""

from __future__ import annotations

import pytest

from src.readmodel.cache import DashboardTTLCache
from src.readmodel.dashboard_service import DashboardService
from src.thesis.service import CreateThesisInput, ThesisService

USER = "dash_user"


@pytest.fixture(autouse=True)
def _clear_readmodel_cache():
    # DashboardService giữ TTL cache module-level → xoá giữa các test.
    from src.readmodel import dashboard_service as _mod

    cache: DashboardTTLCache = _mod._cache
    cache.invalidate_all()
    yield
    cache.invalidate_all()


async def test_theses_list_empty_user(session):
    svc = DashboardService(session)
    rows = await svc.get_theses_list(user_id=USER, status="all")
    assert rows == []


async def test_theses_list_one_active(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="HPG", title="Steel play"))
    await session.flush()

    svc = DashboardService(session)
    rows = await svc.get_theses_list(user_id=USER, status="active")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "HPG"
    assert rows[0]["status"] == "active"
    assert rows[0]["n_assumptions"] == 0
    assert rows[0]["invalid_assumption_count"] == 0


async def test_theses_list_status_filter(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="VCB", title="Bank"))
    t2 = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="FPT", title="Tech"))
    t3 = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="VNM", title="Dairy"))
    await session.flush()
    await thesis_svc.close(thesis_id=t2.id, user_id=USER)
    await thesis_svc.invalidate(thesis_id=t3.id, user_id=USER)
    await session.flush()

    svc = DashboardService(session)
    all_rows = await svc.get_theses_list(user_id=USER, status="all")
    assert len(all_rows) == 3
    by_status = {r["status"] for r in all_rows}
    assert by_status == {"active", "closed", "invalidated"}

    active = await svc.get_theses_list(user_id=USER, status="active")
    assert [r["ticker"] for r in active] == ["VCB"]


async def test_theses_list_upside_and_rr_computed(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(
        CreateThesisInput(
            user_id=USER,
            ticker="HPG",
            title="Upside test",
            entry_price=20_000,
            target_price=30_000,
            stop_loss=16_000,
        )
    )
    await session.flush()

    svc = DashboardService(session)
    row = (await svc.get_theses_list(user_id=USER))[0]
    assert row["upside_pct"] == pytest.approx(50.0)
    # R/R = (30k-20k) / (20k-16k) = 10k/4k = 2.5
    assert row["risk_reward"] == pytest.approx(2.5)


async def test_theses_list_no_cross_user_leak(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(CreateThesisInput(user_id="other_user", ticker="TCB", title="Other"))
    await session.flush()

    svc = DashboardService(session)
    assert await svc.get_theses_list(user_id=USER, status="all") == []


async def test_theses_list_last_verdict_populated(session):
    from src.ai.agents.thesis_review import ThesisReviewAgent
    from src.thesis.review_service import ReviewService
    from tests.ai.conftest import MockPerplexityClient

    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="MWG", title="Retail"))
    await session.flush()

    mock = MockPerplexityClient(
        {
            "verdict": "BULLISH",
            "confidence": 0.9,
            "risk_signals": [],
            "next_watch_items": [],
            "reasoning": "Strong thesis.",
            "assumption_updates": [],
            "catalyst_status": [],
        }
    )
    review_svc = ReviewService(session=session, agent=ThesisReviewAgent(mock))
    await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    svc = DashboardService(session)
    row = (await svc.get_theses_list(user_id=USER))[0]
    assert row["last_verdict"] is not None
    assert "bullish" in row["last_verdict"].lower()
    assert row["last_confidence"] == pytest.approx(0.9)


async def test_thesis_detail_includes_components(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(
            user_id=USER,
            ticker="SSI",
            title="Broker",
            assumptions=["Thanh khoản tăng"],
            catalysts=["KRX go-live"],
        )
    )
    await session.flush()

    svc = DashboardService(session)
    detail = await svc.get_thesis_detail(user_id=USER, thesis_id=thesis.id)
    assert detail is not None
    assert [a["description"] for a in detail["assumptions"]] == ["Thanh khoản tăng"]
    assert [c["description"] for c in detail["catalysts"]] == ["KRX go-live"]
    # Ownership guard
    assert await svc.get_thesis_detail(user_id="other_user", thesis_id=thesis.id) is None
