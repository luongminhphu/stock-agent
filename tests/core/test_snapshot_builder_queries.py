"""mypy M2 — SystemSnapshotBuilder truy vấn đúng cột ORM thật.

Bản cũ dùng Alert.dismissed_at / Alert.alert_type / ThesisReview.created_at /
Position.stop_loss_breached (không tồn tại) → mọi nhánh raise rồi bị nuốt →
snapshot luôn rỗng và engine không bao giờ thấy alert/thesis/risk.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.snapshot import SystemSnapshotBuilder
from src.portfolio.models import Position
from src.thesis.models import ReviewVerdict, Thesis, ThesisReview, ThesisStatus
from src.watchlist.models import Alert, AlertConditionType, AlertStatus

USER = "u-snap"


async def _seed(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    t_overdue = Thesis(user_id=USER, ticker="HPG", title="HPG thesis", status=ThesisStatus.ACTIVE)
    t_fresh = Thesis(user_id=USER, ticker="VCB", title="VCB thesis", status=ThesisStatus.ACTIVE)
    t_bad = Thesis(user_id=USER, ticker="NVL", title="NVL", status=ThesisStatus.INVALIDATED)
    session.add_all([t_overdue, t_fresh, t_bad])
    await session.flush()
    session.add(
        ThesisReview(
            thesis_id=t_overdue.id,
            verdict=ReviewVerdict.NEUTRAL,
            confidence=0.6,
            reasoning="cũ",
            reviewed_at=now - timedelta(days=45),
        )
    )
    session.add(
        ThesisReview(
            thesis_id=t_fresh.id,
            verdict=ReviewVerdict.NEUTRAL,
            confidence=0.6,
            reasoning="mới",
            reviewed_at=now - timedelta(days=1),
        )
    )
    session.add(
        Alert(
            user_id=USER,
            ticker="HPG",
            condition_type=AlertConditionType.PRICE_ABOVE,
            threshold=30.0,
            status=AlertStatus.TRIGGERED,
            triggered_at=now - timedelta(hours=1),
        )
    )
    session.add(
        Alert(
            user_id=USER,
            ticker="VCB",
            condition_type=AlertConditionType.PRICE_BELOW,
            threshold=80.0,
            status=AlertStatus.DISMISSED,
            triggered_at=now - timedelta(hours=2),
        )
    )
    session.add(Position(user_id=USER, ticker="NVL", qty=100, avg_cost=10.0, thesis_id=t_bad.id))
    session.add(
        Position(user_id=USER, ticker="HPG", qty=100, avg_cost=25.0, thesis_id=t_overdue.id)
    )
    await session.commit()


async def test_snapshot_builder_sees_alerts_thesis_and_risk(session: AsyncSession) -> None:
    await _seed(session)
    snap = await SystemSnapshotBuilder(session, USER).build()

    # Alert: chỉ TRIGGERED, alert_type = condition_type.value
    assert [a.ticker for a in snap.watchlist_alerts] == ["HPG"]
    assert snap.watchlist_alerts[0].alert_type == "price_above"

    # Thesis quá hạn review (>30 ngày) — VCB vừa review nên không nằm trong danh sách
    due = {t.ticker: t.days_overdue for t in snap.thesis_due_review}
    assert "HPG" in due and due["HPG"] >= 44
    assert "VCB" not in due

    # risk_breach_count = vị thế mở gắn thesis INVALIDATED/WEAKENING
    assert snap.portfolio.total_positions == 2
    assert snap.portfolio.risk_breach_count == 1
