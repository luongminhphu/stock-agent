"""Wave E3a — FeedbackLedgerSubscriber dual-write vào user_behavior_logs."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.ai.memory.feedback_ledger import FeedbackLedgerSubscriber
from src.ai.memory.user_behavior_log import UserBehaviorLog
from src.platform.db import AsyncSessionLocal
from src.platform.event_bus import EventBus
from src.platform.events import (
    BriefFeedbackRecordedEvent,
    EngineFeedbackSubmittedEvent,
    PretradeAdviceReconciledEvent,
)


async def _rows(user_id: str) -> list[UserBehaviorLog]:
    async with AsyncSessionLocal() as s:
        res = await s.execute(
            select(UserBehaviorLog)
            .where(UserBehaviorLog.user_id == user_id)
            .order_by(UserBehaviorLog.id)
        )
        return list(res.scalars().all())


def test_register_subscribes_three_events() -> None:
    bus = EventBus()
    FeedbackLedgerSubscriber(bus).register()
    for ev in (
        EngineFeedbackSubmittedEvent,
        BriefFeedbackRecordedEvent,
        PretradeAdviceReconciledEvent,
    ):
        assert bus._handlers.get(ev), ev.__name__


@pytest.mark.anyio
async def test_engine_brief_pretrade_land_in_ledger() -> None:
    sub = FeedbackLedgerSubscriber(EventBus())
    await sub._on_engine_feedback(
        EngineFeedbackSubmittedEvent(
            verdict_event_id="v-1", user_id="u-ledger", verdict="BUY_SIGNAL", outcome="acted"
        )
    )
    await sub._on_brief_feedback(
        BriefFeedbackRecordedEvent(
            brief_snapshot_id=7, user_id="u-ledger", outcome="skipped", brief_type="morning"
        )
    )
    await sub._on_pretrade_reconciled(
        PretradeAdviceReconciledEvent(
            user_id="u-ledger",
            ticker="hpg",
            decision_log_id=42,
            advice_verdict="BEARISH",
            action_type="BUY",
            adherence="ignored_advice",
        )
    )
    rows = await _rows("u-ledger")
    assert [(r.source, r.signal, r.ref_type, r.ref_id) for r in rows] == [
        ("core", "engine:acted", "verdict", "v-1"),
        ("briefing", "brief:skipped", "brief", "7"),
        ("thesis", "ignored_advice", "pretrade", "42"),
    ]
    assert rows[2].ticker == "HPG" and rows[1].agent_type == "briefing:morning"


@pytest.mark.anyio
async def test_missing_user_is_skipped() -> None:
    sub = FeedbackLedgerSubscriber(EventBus())
    await sub._on_engine_feedback(EngineFeedbackSubmittedEvent(verdict_event_id="v-2", user_id=""))
    assert await _rows("") == []
